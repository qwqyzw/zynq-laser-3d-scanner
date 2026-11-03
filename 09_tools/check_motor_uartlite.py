#!/usr/bin/env python3
"""Offline fault-injection tests for the combined overlay UART transport."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '04_runtime'))
from motor_readonly import ProtocolError
from motor_position import ArduinoAngleController
from motor_uartlite import (
    UARTLiteTransport, UARTLiteReadOnlyClient, UARTLiteAngleController,
    RX_FIFO, TX_FIFO, STATUS, CONTROL, RX_VALID, TX_EMPTY, TX_FULL)


CONFIG = bytes.fromhex(
    '01 42 21 15 19 02 02 02 00 10 01 00 04 b0 0b b8 13 88 00 07 '
    '01 00 01 01 00 08 08 98 07 d0 00 08 6b')


class FakeClock:
    def __init__(self):
        self.now = 0.

    def monotonic(self):
        return self.now

    def sleep(self, amount):
        self.now += max(amount, .000001)


class FakeUART:
    """Model serial arrival at 9600 baud and a 16-byte RX FIFO."""
    def __init__(self, clock, reply=b'', request_size=3, *, initial=b'',
                 status_error=0, tx_stuck=False):
        self.clock = clock
        self.reply = reply
        self.request_size = request_size
        self.status_error = status_error
        self.tx_stuck = tx_stuck
        self.pending = [(0., byte) for byte in initial]
        self.rx = []
        self.tx = []
        self.writes = []

    def read(self, offset):
        while self.pending and self.pending[0][0] <= self.clock.now:
            self.rx.append(self.pending.pop(0)[1])
        if offset == STATUS:
            return ((TX_FULL if self.tx_stuck else TX_EMPTY) |
                    (RX_VALID if self.rx else 0) | self.status_error |
                    (0x20 if len(self.rx) > 16 else 0))
        if offset == RX_FIFO:
            return self.rx.pop(0)
        raise AssertionError('Unexpected register read')

    def write(self, offset, value):
        self.writes.append((offset, value))
        if offset == CONTROL:
            if value != 0:
                raise AssertionError('No FIFO reset permitted')
            return
        if offset != TX_FIFO:
            raise AssertionError('Unexpected register write')
        self.tx.append(value)
        if len(self.tx) == self.request_size:
            self.pending.extend(
                (self.clock.now + .002 + index * (10 / 9600), byte)
                for index, byte in enumerate(self.reply))


def client(reply, *, request_size=3, **kwargs):
    clock = FakeClock()
    uart = FakeUART(clock, reply, request_size, **kwargs)
    transport = UARTLiteTransport(uart, clock=clock.monotonic, sleep=clock.sleep)
    return UARTLiteReadOnlyClient(None, transport=transport), uart, clock


class UARTTests(unittest.TestCase):
    def test_version_and_exact_one_request(self):
        instance, uart, _ = client(bytes.fromhex('01 1f 00 c9 23 14 6b'))
        self.assertEqual(instance.query('version')['firmware_version_code'], 201)
        self.assertEqual(bytes(uart.tx), bytes.fromhex('01 1f 6b'))
        self.assertEqual(instance.transactions[0]['sent_bytes'], 3)

    def test_configuration_longer_than_fifo(self):
        instance, uart, _ = client(CONFIG, request_size=4)
        result = instance.query('configuration')
        self.assertEqual(result['firmware_family_layout'], 'Emm')
        self.assertEqual(result['raw_configuration_hex'], CONFIG.hex(' '))
        self.assertFalse(instance.transactions[0]['uart_status_observed_or'] & 0x20)

    def test_position_signed_emm(self):
        instance, _, _ = client(bytes.fromhex('01 36 01 00 00 00 05 6b'))
        self.assertAlmostEqual(instance.query('position', 'Emm')['motor_position_deg'],
                               -5 * 360 / 65536)

    def test_missing_response_is_bounded_and_never_retried(self):
        instance, uart, clock = client(b'')
        with self.assertRaisesRegex(ProtocolError, 'timeout'):
            instance.query('voltage')
        self.assertLessEqual(clock.now, .501)
        self.assertEqual(bytes(uart.tx), bytes.fromhex('01 24 6b'))
        self.assertIn('error', instance.transactions[0])

    def test_stale_reply_prevents_request(self):
        instance, uart, _ = client(b'', initial=bytes.fromhex('01 3a 03 6b'))
        with self.assertRaisesRegex(ProtocolError, 'stale'):
            instance.query('status')
        self.assertEqual(uart.tx, [])
        self.assertEqual(instance.transactions[0]['stale_rx_hex'], '01 3a 03 6b')

    def test_wrong_address_function_checksum_and_extra_bytes(self):
        for raw in ('02 3a 03 6b', '01 24 03 6b', '01 3a 03 00', '01 3a 03 6b 01'):
            with self.subTest(raw=raw):
                instance, uart, _ = client(bytes.fromhex(raw))
                with self.assertRaises(ProtocolError):
                    instance.query('status')
                self.assertEqual(bytes(uart.tx), bytes.fromhex('01 3a 6b'))

    def test_unknown_config_length(self):
        instance, _, _ = client(bytes.fromhex('01 42 20 15'), request_size=4)
        with self.assertRaisesRegex(ProtocolError, 'configuration response length'):
            instance.query('configuration')

    def test_device_rejection(self):
        instance, _, _ = client(bytes.fromhex('01 42 e2 6b'), request_size=4)
        with self.assertRaisesRegex(ProtocolError, 'rejected query'):
            instance.query('configuration')

    def test_e2_ee_in_ordinary_encoder_payload(self):
        for high, low in ((0xE2, 0x01), (0xEE, 0x6B)):
            with self.subTest(high=high, low=low):
                instance, _, _ = client(bytes([1, 0x31, high, low, 0x6B]))
                self.assertEqual(instance.query('encoder')['encoder_count'],
                                 (high << 8) | low)

    def test_uart_error_stops_before_sending(self):
        for error in (0x20, 0x40, 0x80):
            with self.subTest(error=error):
                instance, uart, _ = client(b'', status_error=error)
                with self.assertRaisesRegex(ProtocolError, 'overrun/framing/parity'):
                    instance.query('status')
                self.assertEqual(uart.tx, [])

    def test_tx_stuck_is_bounded(self):
        instance, uart, clock = client(b'', tx_stuck=True)
        with self.assertRaisesRegex(ProtocolError, 'idle'):
            instance.query('status')
        self.assertLess(clock.now, .101)
        self.assertEqual(uart.tx, [])

    def test_partial_response_is_preserved(self):
        instance, _, _ = client(bytes.fromhex('01 36 00'))
        with self.assertRaises(ProtocolError) as caught:
            instance.query('position', 'Emm')
        self.assertEqual(caught.exception.raw, bytes.fromhex('01 36 00'))
        self.assertEqual(instance.transactions[0]['rx_hex'], '01 36 00')

    def test_close_query_and_concurrent_owner(self):
        instance, uart, _ = client(b'')
        instance.transport._lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, 'Concurrent'):
                instance.query('status')
            with self.assertRaisesRegex(RuntimeError, 'active'):
                instance.close()
        finally:
            instance.transport._lock.release()
        instance.close()
        instance.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            instance.query('status')
        self.assertEqual(uart.tx, [])

    def test_overlay_metadata_rejects_wrong_settings_without_pynq(self):
        overlay = SimpleNamespace(is_loaded=lambda: True, ip_dict={
            'axi_uartlite_0': {'type': 'xilinx.com:ip:axi_uartlite:2.0',
                              'parameters': {'C_BAUDRATE': '115200',
                                             'C_DATA_BITS': '8',
                                             'C_USE_PARITY': '0'}}})
        with self.assertRaisesRegex(ValueError, '9600'):
            UARTLiteTransport.from_overlay(overlay)

    def test_angle_reuses_existing_move_and_gate(self):
        self.assertIs(UARTLiteAngleController.move_to, ArduinoAngleController.move_to)
        self.assertIs(UARTLiteAngleController.position, ArduinoAngleController.position)
        self.assertIs(UARTLiteAngleController.status, ArduinoAngleController.status)

    def test_stop_and_finite_command_format(self):
        for operation, frame, plan in (
                (0, '01 fe 98 00 6b', None),
                (1, '01 f3 ab 01 00 6b', None),
                (2, '01 fd 00 00 01 0a 00 00 00 2c 02 00 6b',
                 {'direction': 0, 'rpm': 1, 'pulses': 44})):
            with self.subTest(operation=operation):
                raw = bytes.fromhex(frame)
                instance, uart, _ = client(bytes([1, raw[1], 2, 0x6b]),
                                           request_size=len(raw))
                controller = UARTLiteAngleController(
                    SimpleNamespace(bitfile_name='combined.bit'), transport=instance.transport)
                controller._exchange(operation, plan)
                self.assertEqual(bytes(uart.tx), raw)
                self.assertEqual(controller.motion_log[0]['rx_hex'],
                                 bytes([1, raw[1], 2, 0x6b]).hex(' '))

    def test_failed_command_is_not_resent_and_close_still_releases(self):
        instance, uart, _ = client(b'', request_size=5)
        controller = UARTLiteAngleController(
            SimpleNamespace(bitfile_name='combined.bit'), transport=instance.transport)
        with self.assertRaises(ProtocolError):
            controller.close()
        self.assertTrue(controller.closed)
        self.assertTrue(instance.transport.closed)
        self.assertEqual(bytes(uart.tx), bytes.fromhex('01 fe 98 00 6b'))
        controller.close()
        self.assertEqual(len(uart.tx), 5)

    def test_stop_cleans_stale_reply_before_one_stop(self):
        instance, uart, _ = client(bytes.fromhex('01 fe 02 6b'), request_size=5,
                                   initial=bytes.fromhex('01 fd 02 6b'))
        controller = UARTLiteAngleController(
            SimpleNamespace(bitfile_name='combined.bit'), transport=instance.transport)
        controller.close()
        self.assertTrue(controller.closed)
        self.assertEqual(bytes(uart.tx), bytes.fromhex('01 fe 98 00 6b'))
        self.assertEqual(controller.motion_log[0]['stale_rx_hex'], '01 fd 02 6b')

    def test_nonstop_cannot_discard_stale(self):
        instance, uart, _ = client(b'')
        with self.assertRaisesRegex(ValueError, 'Only a stop'):
            instance.transport.exchange(bytes.fromhex('01 fd 6b'), lambda p: 4,
                                        discard_stale=True)
        self.assertEqual(uart.tx, [])

    def test_keyboard_interrupt_releases_exchange_lock(self):
        instance, _, _ = client(b'')
        with patch.object(instance.transport, '_status', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                instance.query('status')
        self.assertTrue(instance.transport._lock.acquire(False))
        instance.transport._lock.release()


if __name__ == '__main__':
    unittest.main(verbosity=2)
