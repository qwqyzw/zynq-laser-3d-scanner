"""Bounded polled AXI UARTLite transport for the combined PL + motor overlay.

No PYNQ import, bit download, UART write or motor command occurs on import.
Constructors bind the already loaded 9600/8N1 ``axi_uartlite_0``. The angle
controller reuses the established Emm protocol, movement limits and settle gate.

Register definitions: AMD embeddedsw drivers/uartlite/src/xuartlite_l.h.
Interrupts are disabled; FIFO resets are deliberately not used to abort a
possibly queued command. Failed requests are never resent automatically.
"""
import hashlib
from pathlib import Path
import threading
import time

from motor_readonly import ProtocolError, decode_response, request_frame, response_length
from motor_position import ArduinoAngleController, position_frame


RX_FIFO, TX_FIFO, STATUS, CONTROL = 0x00, 0x04, 0x08, 0x0C
RX_VALID, TX_EMPTY, TX_FULL, UART_ERRORS = 0x01, 0x04, 0x08, 0xE0


class UARTLiteTransport:
    """One owner, bounded exchanges, strict replies and recorded stale bytes."""

    def __init__(self, mmio, *, clock=time.monotonic, sleep=time.sleep):
        self.mmio = mmio
        self.clock = clock
        self.sleep = sleep
        self.closed = False
        self._lock = threading.Lock()
        # No reset: clearing queued TX bytes could truncate an earlier command.
        self.mmio.write(CONTROL, 0)

    @classmethod
    def from_overlay(cls, overlay, ip_name='axi_uartlite_0'):
        if not callable(getattr(overlay, 'is_loaded', None)) or not overlay.is_loaded():
            raise RuntimeError('Bind UART only after the chosen overlay is loaded')
        info = overlay.ip_dict.get(ip_name)
        if not info or info.get('type') != 'xilinx.com:ip:axi_uartlite:2.0':
            raise ValueError('Loaded overlay lacks the expected AXI UARTLite 2.0')
        parameters = info.get('parameters', {})
        for name, expected in (
                ('C_BAUDRATE', 9600), ('C_DATA_BITS', 8), ('C_USE_PARITY', 0)):
            try:
                value = int(str(parameters[name]), 0)
            except (KeyError, TypeError, ValueError):
                raise ValueError('UART HWH is missing verified parameter ' + name)
            if value != expected:
                raise ValueError('UART HWH must specify 9600 baud, 8 bits, no parity')
        if int(info['addr_range']) < 16:
            raise ValueError('UART MMIO window is too small')
        from pynq import MMIO
        return cls(MMIO(info['phys_addr'], info['addr_range']))

    def _status(self, entry):
        status = int(self.mmio.read(STATUS))
        entry['uart_status'] = status
        entry['uart_status_observed_or'] |= status
        if status & UART_ERRORS:
            raise ProtocolError('UART overrun/framing/parity error')
        return status

    def _drain_before_send(self, deadline, entry, discard_stale):
        # At 9600 baud 10 ms exceeds nine byte times; all immediately preceding
        # bytes must drain and the transmitter must be empty before a request.
        quiet_since = None
        stale = bytearray()
        while self.clock() < deadline:
            status = self._status(entry)
            if status & RX_VALID:
                stale.append(int(self.mmio.read(RX_FIFO)) & 0xFF)
                entry['stale_rx_hex'] = stale.hex(' ')
                if len(stale) >= 64:
                    raise ProtocolError('Excess stale UART bytes; request was not sent', bytes(stale))
                quiet_since = None
            elif status & TX_EMPTY:
                if quiet_since is None:
                    quiet_since = self.clock()
                if self.clock() - quiet_since >= .01:
                    if stale and not discard_stale:
                        raise ProtocolError('Unexpected stale UART bytes; request was not sent', bytes(stale))
                    return
            else:
                quiet_since = None
            self.sleep(.0005)
        raise ProtocolError('UART did not become idle; request was not sent', bytes(stale))

    def exchange(self, frame, expected_length, *, timeout_ms=500, entry=None,
                 discard_stale=False):
        """Send exactly once and receive a single matching protocol response.

        ``expected_length(prefix)`` supports the variable-length config read.
        The last byte is checked as fixed 0x6B. The caller interprets payloads.
        Any late/additional bytes during the 10 ms tail window are an error.
        Only the stop command may discard recorded stale bytes before sending;
        this lets failure cleanup stop the motor despite an old reply.
        """
        if self.closed:
            raise RuntimeError('UART transport already closed')
        if type(timeout_ms) is not int or not 50 <= timeout_ms <= 2000:
            raise ValueError('UART timeout must be integer 50..2000 ms')
        if not isinstance(frame, bytes) or not 3 <= len(frame) <= 13:
            raise ValueError('Expected one bounded motor protocol frame')
        if not callable(expected_length):
            raise TypeError('Expected a response length function')
        if discard_stale and frame[1:] != bytes([0xFE, 0x98, 0, 0x6B]):
            raise ValueError('Only a stop may discard stale receive bytes')
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('Concurrent UART request rejected; use one motor owner')
        if entry is None:
            entry = {}
        entry.update(tx_hex=frame.hex(' '), rx_hex='', sent_bytes=0,
                     stale_rx_hex='', uart_status=0, uart_status_observed_or=0)
        raw = bytearray()
        started = self.clock()
        deadline = started + timeout_ms / 1000
        complete_at = None
        try:
            self._drain_before_send(min(deadline, started + .1), entry, discard_stale)
            while self.clock() < deadline:
                status = self._status(entry)
                if entry['sent_bytes'] < len(frame) and not status & TX_FULL:
                    self.mmio.write(TX_FIFO, frame[entry['sent_bytes']])
                    entry['sent_bytes'] += 1
                    # Poll promptly while filling the <=13-byte command.
                    continue
                if status & RX_VALID:
                    raw.append(int(self.mmio.read(RX_FIFO)) & 0xFF)
                    entry['rx_hex'] = raw.hex(' ')
                    if len(raw) > 64:
                        raise ProtocolError('Motor response exceeds capacity', bytes(raw))
                    if raw[0] != frame[0] or (len(raw) >= 2 and raw[1] != frame[1]):
                        raise ProtocolError('Motor response address/function mismatch', bytes(raw))
                    if len(raw) >= 3:
                        # E2/EE can also occur in a normal payload (for example
                        # an encoder high byte). A 4-byte rejection is only a
                        # candidate until the quiet tail proves no normal
                        # payload byte follows it.
                        if len(raw) == 3 and raw[2] in (0xE2, 0xEE):
                            expected = None
                        elif (len(raw) == 4 and raw[2] in (0xE2, 0xEE)
                              and raw[3] == 0x6B):
                            expected = 4
                        else:
                            expected = expected_length(bytes(raw))
                        if expected is not None:
                            if not 4 <= expected <= 64:
                                raise ProtocolError('Unsupported motor response length', bytes(raw))
                            if len(raw) > expected:
                                raise ProtocolError('Additional bytes after motor reply', bytes(raw))
                            if len(raw) == expected:
                                if raw[-1] != 0x6B:
                                    raise ProtocolError('Wrong fixed checksum byte', bytes(raw))
                                complete_at = self.clock()
                            else:
                                complete_at = None
                    continue
                if (complete_at is not None and entry['sent_bytes'] == len(frame)
                        and status & TX_EMPTY and self.clock() - complete_at >= .01):
                    entry['return_code'] = len(raw)
                    return bytes(raw)
                self.sleep(min(.0005, max(0, deadline - self.clock())))
            raise ProtocolError('Motor UART response timeout; command is not resent', bytes(raw))
        except BaseException as exc:
            entry['error'] = '%s: %s' % (type(exc).__name__, exc)
            entry['return_code'] = -1
            if isinstance(exc, ProtocolError) and not exc.raw:
                exc.raw = bytes(raw)
            raise
        finally:
            entry['rx_hex'] = raw.hex(' ')
            entry['duration_s'] = self.clock() - started
            self._lock.release()

    def close(self):
        """Release this software owner; sends no motor command or FIFO reset."""
        if self.closed:
            return
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('Cannot close UART during an active exchange')
        try:
            self.closed = True
        finally:
            self._lock.release()


class UARTLiteReadOnlyClient:
    """Same read-query API as ArduinoReadOnlyClient, without MicroBlaze."""

    def __init__(self, overlay, address=1, *, transport=None):
        request_frame('version', address)
        self.transport = (UARTLiteTransport.from_overlay(overlay) if transport is None
                          else transport)
        self.address = address
        self.transactions = []
        self.closed = False

    def query(self, name, firmware_family=None, timeout_ms=500):
        if self.closed:
            raise RuntimeError('Motor client already closed')
        frame = request_frame(name, self.address)
        entry = {'query': name}
        self.transactions.append(entry)
        try:
            raw = self.transport.exchange(
                frame, lambda prefix: response_length(name, prefix),
                timeout_ms=timeout_ms, entry=entry)
            result = decode_response(name, raw, self.address, firmware_family)
            entry['result'] = result
            return result
        except BaseException as exc:
            entry['error'] = '%s: %s' % (type(exc).__name__, exc)
            raise

    def close(self):
        if not self.closed:
            self.transport.close()
            self.closed = True


class UARTLiteAngleController(ArduinoAngleController):
    """Existing finite movement/settle logic with a direct AXI UART backend."""

    def __init__(self, overlay, address=1, *, transport=None):
        # Do not call the Arduino constructor: it loads MicroBlaze firmware.
        self._client = UARTLiteReadOnlyClient(overlay, address, transport=transport)
        self.address = address
        self.transactions = self._client.transactions
        self.geometry = None
        self.prepared = False
        self.closed = False
        self.motion_log = []
        root = Path(__file__).parent
        digest = hashlib.sha256()
        for name in ('motor_uartlite.py', 'motor_position.py', 'motor_readonly.py'):
            digest.update(name.encode('ascii') + b'\0' + (root / name).read_bytes())
        self.source_sha256 = digest.hexdigest()
        self.overlay_bitfile = str(overlay.bitfile_name)

    def query(self, name, firmware_family=None, timeout_ms=500):
        return self._client.query(name, firmware_family, timeout_ms)

    def _exchange(self, operation, plan=None):
        if self.closed:
            raise RuntimeError('Motor client already closed')
        if type(operation) is not int or operation not in (0, 1, 2):
            raise ValueError('Only stop, enable and finite relative positioning are supported')
        frame = (position_frame(self.address, plan) if operation == 2 else
                 bytes([self.address, 0xFE, 0x98, 0, 0x6B]) if operation == 0 else
                 bytes([self.address, 0xF3, 0xAB, 1, 0, 0x6B]))
        entry = {'operation': operation}
        self.motion_log.append(entry)
        raw = self._client.transport.exchange(
            frame, lambda prefix: 4, entry=entry, discard_stale=operation == 0)
        if raw != bytes([self.address, frame[1], 2, 0x6B]):
            entry['error'] = 'No valid command acceptance; command is not resent'
            raise ProtocolError(entry['error'], raw)
        return entry

    def prepare(self):
        result = super().prepare()
        result.update(backend='hardware_pynq_uartlite', uart_ip='axi_uartlite_0',
                      baudrate=9600, uart_format='8N1',
                      transport='direct_axi_mmio_polling',
                      source_files=['motor_uartlite.py', 'motor_position.py',
                                    'motor_readonly.py'])
        return result

    def close(self):
        if self.closed:
            return
        try:
            self.stop()
        finally:
            self._client.close()
            self.closed = True
