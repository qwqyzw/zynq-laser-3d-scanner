"""Pure fake-MMIO checks for one-request diagnostics; never imports PYNQ."""
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch

import diagnose_motor_uart as diagnostic


class FakeTime:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class FakeUART:
    def __init__(self, timer, *, stale=False, initial_error=False,
                 persistent_error=False, active_error=False, tx_stuck=False,
                 fail_write=None, fail_read_after=None):
        self.timer = timer
        self.stale, self.initial_error = stale, initial_error
        self.persistent_error, self.active_error = persistent_error, active_error
        self.tx_stuck, self.fail_write = tx_stuck, fail_write
        self.fail_read_after = fail_read_after
        self.writes, self.read_offsets = [], []
        self.first_write = None
        self.error_delivered = False
        self.response = b'\x01\x1f\x00\xc9\x23\x14\x6b'
        self.received = 0

    def read(self, offset):
        self.read_offsets.append(offset)
        if offset == diagnostic.RX_FIFO:
            if self.fail_read_after == self.received:
                raise OSError('mock RX read failed')
            value = self.response[self.received]
            self.received += 1
            return value
        if offset != diagnostic.STATUS:
            raise AssertionError('Unexpected MMIO read')
        if self.first_write is None:
            initial = self.initial_error and len(self.read_offsets) == 1
            return diagnostic.TX_EMPTY | (diagnostic.RX_VALID if self.stale else 0) | (
                0x40 if initial or self.persistent_error else 0)
        elapsed = self.timer.clock() - self.first_write
        value = 0 if self.tx_stuck or elapsed < .003 else diagnostic.TX_EMPTY
        if elapsed >= .012 and self.received < len(self.response):
            value |= diagnostic.RX_VALID
        if self.active_error and elapsed >= .001 and not self.error_delivered:
            self.error_delivered = True
            value |= 0x40
        return value

    def write(self, offset, value):
        self.writes.append((offset, value))
        if self.first_write is None:
            self.first_write = self.timer.clock()
        if self.fail_write == len(self.writes):
            raise OSError('mock TX write failed')


class DiagnosticChecks(unittest.TestCase):
    def sample(self, **kwargs):
        timer = FakeTime()
        uart = FakeUART(timer, **kwargs)
        report = diagnostic.sample_version(uart, clock=timer.clock, sleep=timer.sleep)
        return report, uart

    def test_clean_version_sends_only_fixed_request_once(self):
        report, uart = self.sample()
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(uart.writes, [(4, 1), (4, 0x1F), (4, 0x6B)])
        self.assertEqual(report['sent_to_fifo_count'], 3)
        self.assertEqual(report['decoded_version']['firmware_version_code'], 201)
        self.assertEqual(report['raw_rx_hex'], '01 1f 00 c9 23 14 6b')
        self.assertGreaterEqual(report['observation_elapsed_s'], .350)
        self.assertGreaterEqual(report['passive_final_quiet_s'], .020)
        self.assertIsNotNone(report['tx_fifo_empty_relative_to_first_tx_s'])
        self.assertFalse(report['tx_wire_idle_confirmed'])

    def test_frame_error_keeps_capturing_later_reply_but_never_passes(self):
        report, uart = self.sample(active_error=True)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['raw_rx_hex'], '01 1f 00 c9 23 14 6b')
        self.assertEqual(len(uart.writes), 3)
        self.assertGreaterEqual(report['first_active_error_relative_to_first_tx_s'], .001)
        self.assertTrue(any(row['errors'] == 0x40 for row in report['status_trace']))
        self.assertEqual(len(report['rx_bytes']), 7)
        self.assertFalse(report['recovery_required'])

    def test_existing_rx_is_not_read_and_request_is_refused(self):
        report, uart = self.sample(stale=True)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(uart.writes, [])
        self.assertNotIn(diagnostic.RX_FIFO, uart.read_offsets)
        self.assertIn('existing_rx_bytes_not_read', report['passive_refusal_reasons'])

    def test_initial_read_clear_error_remains_evidence_despite_good_reply(self):
        report, uart = self.sample(initial_error=True)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['initial_read_clear_errors'], 0x40)
        self.assertTrue(report['initial_error_diagnostic_exception_used'])
        self.assertLess(report['first_error_relative_to_first_tx_s'], 0)
        self.assertIsNone(report['first_active_error_s'])
        self.assertEqual(report['decoded_version']['firmware_version_code'], 201)
        self.assertEqual(len(uart.writes), 3)

    def test_persistent_error_refuses_all_writes(self):
        report, uart = self.sample(persistent_error=True)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(uart.writes, [])
        self.assertIn('passive_error_after_initial_sample', report['passive_refusal_reasons'])
        self.assertIn('final_twenty_ms_not_clean', report['passive_refusal_reasons'])

    def test_tx_still_queued_requires_recovery_even_with_complete_reply(self):
        report, uart = self.sample(tx_stuck=True)
        self.assertEqual(report['status'], 'recovery_required')
        self.assertTrue(report['recovery_required'])
        self.assertTrue(report['service_restart_inhibited'])
        self.assertIsNone(report['tx_fifo_empty_observed_s'])
        self.assertEqual(len(uart.writes), 3)

    def test_partial_write_exception_preserves_attempts_and_requires_recovery(self):
        report, uart = self.sample(fail_write=2)
        self.assertEqual(report['status'], 'recovery_required')
        self.assertEqual(report['sent_to_fifo_count'], 1)
        self.assertEqual(len(report['tx_fifo_writes']), 2)
        self.assertFalse(report['tx_fifo_writes'][1]['write_returned'])
        self.assertIn('mock TX write failed', report['error'])
        self.assertEqual(len(uart.writes), 2)

    def test_rx_read_exception_preserves_prefix_and_fails(self):
        report, _ = self.sample(fail_read_after=2)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['raw_rx_hex'], '01 1f')
        self.assertIn('mock RX read failed', report['error'])
        self.assertTrue(report['status_trace'])

    def test_default_check_only_never_runs_board(self):
        with patch.object(diagnostic, 'inspect_project', return_value={}), \
                patch.object(diagnostic, 'run_board') as board, redirect_stdout(io.StringIO()):
            self.assertEqual(diagnostic.main([]), 0)
            board.assert_not_called()


if __name__ == '__main__':
    unittest.main()
