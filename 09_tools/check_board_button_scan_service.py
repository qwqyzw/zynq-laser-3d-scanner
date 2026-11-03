"""Synthetic button timing and coordinator ownership checks. No hardware or XYZ."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
import board_button_scan_service as service


class ButtonChecks(unittest.TestCase):
    def gate(self):
        return service.ButtonGate(debounce=.08, hold=1)

    def test_boot_held_never_starts_until_release(self):
        gate = self.gate()
        self.assertIsNone(gate.update(1, 0, False))
        self.assertIsNone(gate.update(1, 2, False))
        self.assertIsNone(gate.update(1, 5, False))
        gate.update(0, 6, False); gate.update(0, 6.1, False)
        gate.update(1, 7, False); gate.update(1, 7.1, False)
        self.assertEqual(gate.update(1, 8.2, False), 'start')
        self.assertIsNone(gate.update(1, 10, False))

    def test_bounce_and_short_press_do_not_start(self):
        gate = self.gate(); gate.update(0, 0, False);gate.update(0, .1, False)
        for bits, now in ((1, 1), (0, 1.02), (1, 1.04), (0, 1.06), (0, 2)):
            self.assertIsNone(gate.update(bits, now, False))
        gate.update(1, 3, False);gate.update(1, 3.1, False)
        self.assertIsNone(gate.update(1, 3.5, False))
        self.assertIsNone(gate.update(0, 3.6, False))

    def test_busy_presses_are_not_queued(self):
        gate = self.gate();gate.update(0, 0, False);gate.update(0, .1, False)
        gate.update(1, 1, True);gate.update(1, 1.1, True)
        self.assertIsNone(gate.update(1, 5, False))
        gate.update(0, 6, False);gate.update(0, 6.1, False)
        gate.update(1, 7, False);gate.update(1, 7.1, False)
        self.assertEqual(gate.update(1, 8.2, False), 'start')

    def test_stop_once_per_press_and_no_start_with_stop(self):
        gate = self.gate();gate.update(0, 0, False);gate.update(0, .1, False)
        gate.update(2, 1, True)
        self.assertEqual(gate.update(2, 1.1, True), 'stop')
        self.assertIsNone(gate.update(2, 3, True))
        gate.update(3, 4, False)
        self.assertIsNone(gate.update(3, 7, False))

    def test_recovery_owner_is_retained_without_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = root / 'config.json';config_file.write_text('{}')
            coordinator = service.Coordinator({'output_root': root, 'mode': 'diagnostic_preview',
                'client_date': '2026-10-20', 'config_file': config_file}, {})
            coordinator.run = root / 'runs/test';(coordinator.run / 'scan').mkdir(parents=True)
            service.save(coordinator.run / 'scan/completion.json', {'recovery_hold': True})
            owner = Mock();owner.pid=123;owner.poll.return_value=None
            coordinator.process=owner;coordinator.stage='acquiring'
            coordinator.poll()
            self.assertIs(coordinator.process, owner)
            self.assertEqual(coordinator.state, 'recovery_required')
            owner.kill.assert_not_called();owner.terminate.assert_not_called()
            coordinator.request_stop()
            self.assertTrue((coordinator.run / 'scan/abort.request').is_file())
            owner.stdin.close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
