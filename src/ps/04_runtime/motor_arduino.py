"""PYNQ Arduino UART read-query adapter. No camera or motor motion APIs.

Constructor loads own MicroBlaze code and routes Arduino D1/D0. Call only after
base overlay, wiring,3.3V logic compatibility and matching9600 baud are verified.
Importing the file does not import PYNQ or touch pins.
"""
from pathlib import Path
from motor_readonly import request_frame, decode_response, ProtocolError


class ArduinoReadOnlyClient:
    def __init__(self, overlay, address=1, source=None):
        request_frame('version', address)
        from pynq.lib import MicroblazeRPC
        if not hasattr(overlay, 'iop_arduino'):
            raise ValueError('Loaded overlay has no iop_arduino')
        if source is None:
            source = Path(__file__).with_name('motor_arduino_rpc.c').read_text()
        self.rpc = MicroblazeRPC(overlay.iop_arduino, source)
        self.address = address
        self.transactions = []
        result = int(self.rpc.motor_uart_init())
        if result < 0:
            self.rpc.reset()
            raise RuntimeError('Arduino UART initialization failed')

    def query(self, name, firmware_family=None, timeout_ms=500):
        frame = request_frame(name, self.address)
        if type(timeout_ms) is not int or not 10 <= timeout_ms <= 2000:
            raise ValueError('Timeout must be10..2000ms')
        entry = {'query': name, 'tx_hex': frame.hex(' ')}
        self.transactions.append(entry)
        response = [0] * 64
        result = int(self.rpc.motor_read_query(self.address, frame[1], response, 64, timeout_ms))
        count = int(self.rpc.motor_last_rx_length())
        if not 0 <= count <= 64:
            raise ProtocolError('Invalid MicroBlaze byte count')
        raw = bytes(response[:count])
        entry.update(rx_hex=raw.hex(' '), transport_return_code=result,
                     sent_bytes=int(self.rpc.motor_last_tx_length()),
                     uart_status=int(self.rpc.motor_last_status()))
        if result < 0:
            names = {-1: 'UART not initialized', -2: 'Unsupported query or arguments',
                     -3: 'No complete reply within poll budget', -4: 'Wrong address',
                     -5: 'Wrong function', -6: 'Unknown response layout',
                     -7: 'Wrong fixed checksum byte', -8: 'UART overrun/framing/parity error'}
            entry['error'] = names.get(result, 'Unknown transport error')
            raise ProtocolError(entry['error'], raw)
        decoded = decode_response(name, raw, self.address, firmware_family)
        entry['result'] = decoded
        return decoded

    def close(self):
        self.rpc.reset()
