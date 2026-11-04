"""X42S read-only UART bring-up, based on manufacturer manual V1.0.5.

Importing this module opens no device. Only the fixed-0x6B free protocol is
implemented. Supply, UART levels, port, address and baud must be checked first.
There are no enable, movement, homing, calibration or configuration-write APIs.
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import glob
import json
import time
import uuid


# name: (function, request auxiliary bytes, total response length)
QUERIES = {
    'version': (0x1F, b'', 7),
    'voltage': (0x24, b'', 5),
    'encoder': (0x31, b'', 5),
    'position': (0x36, b'', 8),
    'status': (0x3A, b'', 4),
    'configuration': (0x42, b'\x6c', None),
}


class ProtocolError(RuntimeError):
    def __init__(self, message, raw=b''):
        super().__init__(message)
        self.raw = raw


def request_frame(name, address):
    if type(address) is not int or not 1 <= address <= 255:
        raise ValueError('Use a confirmed unicast address from1 to255')
    if name not in QUERIES:
        raise ValueError('Only named read queries are supported')
    function, auxiliary, _ = QUERIES[name]
    return bytes([address, function]) + auxiliary + b'\x6b'


def response_length(name, prefix):
    length = QUERIES[name][2]
    if length is not None:
        return length
    if len(prefix) < 3:
        return None
    # Manufacturer tables5.8.3 and5.8.5; reject unknown layouts.
    if prefix[2] not in (33, 37):
        raise ProtocolError('Unsupported configuration response length', prefix)
    return prefix[2]


def decode_response(name, frame, address, firmware_family=None):
    request_frame(name, address)
    function = QUERIES[name][0]
    if len(frame) < 4 or frame[:2] != bytes([address, function]):
        raise ProtocolError('Response address/function mismatch or incomplete frame', frame)
    if len(frame) == 4 and frame[2] in (0xE2, 0xEE) and frame[-1] == 0x6B:
        raise ProtocolError('Device rejected query: 0x%02X' % frame[2], frame)
    expected = response_length(name, frame)
    if len(frame) != expected or frame[-1] != 0x6B:
        raise ProtocolError('Unexpected length or fixed checksum byte', frame)
    if name == 'version':
        return {'firmware_version_code': int.from_bytes(frame[2:4], 'big'),
                'hardware_series_code': frame[4] >> 4,
                'hardware_type_code': frame[4] & 15,
                'hardware_revision_code': frame[5]}
    if name == 'configuration':
        family, parameter_count = ('X', 24) if len(frame) == 37 else ('Emm', 21)
        if frame[3] != parameter_count:
            raise ProtocolError('Configuration parameter count mismatch', frame)
        return {'firmware_family_layout': family,
                'parameter_count': parameter_count,
                'note': 'Emm layout does not distinguish ordinary from Turbo firmware',
                'raw_configuration_hex': frame.hex(' ')}
    if name == 'voltage':
        return {'bus_voltage_mV': int.from_bytes(frame[2:4], 'big')}
    if name == 'encoder':
        raw = int.from_bytes(frame[2:4], 'big')
        return {'encoder_count': raw, 'motor_single_turn_angle_deg': raw * 360 / 65536}
    if name == 'position':
        if firmware_family not in ('X', 'Emm') or frame[2] not in (0, 1):
            raise ProtocolError('Position needs verified family and valid sign byte', frame)
        raw = int.from_bytes(frame[3:7], 'big') * (-1 if frame[2] else 1)
        degrees = raw / 10 if firmware_family == 'X' else raw * 360 / 65536
        return {'signed_position_raw': raw, 'motor_position_deg': degrees,
                'note': 'Motor position is not automatically turntable position'}
    flags = frame[2]
    return {'flags_hex': '%02x' % flags, 'enabled': bool(flags & 1),
            'position_reached': bool(flags & 2), 'stall_detected': bool(flags & 4),
            'stall_protection_triggered': bool(flags & 8),
            'left_limit_high': bool(flags & 16), 'right_limit_high': bool(flags & 32),
            'power_off_marker': bool(flags & 128)}


class ReadOnlyClient:
    def __init__(self, serial_port, address, timeout_s=0.5):
        request_frame('version', address)
        if not 0 < timeout_s <= 5:
            raise ValueError('Query timeout must be in(0,5] seconds')
        self.port = serial_port
        self.address = address
        self.timeout_s = timeout_s
        self.transactions = []

    def query(self, name, firmware_family=None):
        command = request_frame(name, self.address)
        entry = {'query': name, 'tx_hex': command.hex(' '), 'rx_hex': ''}
        self.transactions.append(entry)
        response = bytearray()
        try:
            self.port.reset_input_buffer()
            if self.port.write(command) != len(command):
                raise ProtocolError('Incomplete serial write')
            deadline = time.monotonic() + self.timeout_s
            while time.monotonic() < deadline:
                response.extend(self.port.read(1))
                if not response:
                    continue
                if response[0] != self.address:
                    raise ProtocolError('Unexpected address; no automatic retry', bytes(response))
                if len(response) >= 2 and response[1] != QUERIES[name][0]:
                    raise ProtocolError('Unexpected function; no automatic retry', bytes(response))
                # Error replies have four bytes, even for variable-length reads.
                if len(response) == 3 and response[2] in (0xE2, 0xEE):
                    continue
                if len(response) == 4 and response[2] in (0xE2, 0xEE) and response[-1] == 0x6B:
                    raise ProtocolError('Device rejected query', bytes(response))
                if len(response) >= 3:
                    expected = response_length(name, response)
                    if expected is not None and len(response) >= expected:
                        result = decode_response(name, bytes(response), self.address, firmware_family)
                        entry['result'] = result
                        return result
            raise ProtocolError('Query timeout; check connection and configured protocol', bytes(response))
        except Exception as exc:
            entry['error'] = str(exc)
            raise
        finally:
            entry['rx_hex'] = bytes(response).hex(' ')


def port_inventory():
    """List possible ports without opening them or sending any bytes."""
    return {'by_id': sorted(glob.glob('/dev/serial/by-id/*')),
            'device_nodes': sorted(set(glob.glob('/dev/ttyUSB*') +
                                      glob.glob('/dev/ttyACM*') + glob.glob('/dev/cu.*')))}


def probe_snapshot(port_name, baudrate, address, output_dir='motor_results'):
    """Run only after wiring and fixed-0x6B UART settings have been confirmed."""
    request_frame('version', address)
    if type(baudrate) is not int or baudrate <= 0:
        raise ValueError('Provide confirmed baudrate')
    import serial  # pyserial; not imported until a real probe is requested
    report = {'utc': datetime.now(timezone.utc).isoformat(), 'port': port_name,
              'baudrate': baudrate, 'address': address, 'protocol': 'free_fixed_0x6B',
              'read_only_commands': True, 'motion_commands_sent': False,
              'hardware_validated': False, 'results': {}, 'transactions': []}
    path = Path(output_dir) / ('readonly_' + uuid.uuid4().hex[:12] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    device = serial.Serial(port=None, baudrate=baudrate, bytesize=8,
                           parity='N', stopbits=1, timeout=0.05, write_timeout=0.5)
    device.dtr = False
    device.rts = False
    device.port = port_name
    client = ReadOnlyClient(device, address)
    try:
        device.open()
        for name in ['version', 'configuration', 'status', 'voltage', 'encoder', 'position']:
            family = report['results'].get('configuration', {}).get('firmware_family_layout')
            report['results'][name] = client.query(name, firmware_family=family)
        report['communication_checks_passed'] = True
    except Exception as exc:
        report['communication_checks_passed'] = False
        report['error'] = '%s: %s' % (type(exc).__name__, exc)
        raise
    finally:
        device.close()
        report['serial_closed'] = not device.is_open
        report['transactions'] = client.transactions
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('只读通信记录:', path, flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='Execute only read queries')
    parser.add_argument('--port')
    parser.add_argument('--baudrate', type=int)
    parser.add_argument('--address', type=int)
    parser.add_argument('--output-dir', default='motor_results')
    args = parser.parse_args()
    if not args.probe:
        print(json.dumps(port_inventory(), indent=2))
    elif None in (args.port, args.baudrate, args.address):
        parser.error('--probe needs confirmed --port, --baudrate and --address')
    else:
        print(json.dumps(probe_snapshot(args.port, args.baudrate, args.address, args.output_dir),
                         ensure_ascii=False, indent=2))
