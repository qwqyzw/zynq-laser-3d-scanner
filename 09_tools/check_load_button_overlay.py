"""Mock-only boot loader checks. No PYNQ import, board, camera, or UART access."""
from contextlib import ExitStack
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

import load_button_overlay as loader
from object_board_scan import assert_idle


class LoaderChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.button = self.project / 'button.bit'
        self.button.write_bytes(b'mock button overlay')
        self.integrated = self.project / 'integrated.bit'
        self.artifacts = {
            'button_release': {'laser_motor_button.bit': {
                'absolute_path': str(self.button),
                'sha256': hashlib.sha256(self.button.read_bytes()).hexdigest()}},
            'integrated_release': {'laser_motor.bit': {
                'absolute_path': str(self.integrated)}}}
        self.config = {'project': self.project}
        self.pl = SimpleNamespace(bitfile_name=str(self.button))
        self.clocks = SimpleNamespace(fclk0_mhz=50.0)
        self.core = Mock()
        self.core_values = {4: 2, 8: 0x4C535431, 12: 0x10000, 0x38: 56}
        self.core.read.side_effect = lambda offset: self.core_values[offset]
        self.dma = Mock()
        self.dma_values = {0: 1, 4: 0x1002, 0x30: 1, 0x34: 0x1002, 0x58: 2560}
        self.dma.read.side_effect = lambda offset: self.dma_values[offset]
        self.uart = Mock()
        self.uart.read.side_effect = [0x40, 4, 4, 4]
        self.mmio = Mock(side_effect=lambda address, length: {
            0x43C00000: self.core, 0x40400000: self.dma, 0x42C00000: self.uart}[address])
        self.overlay = Mock(side_effect=self.make_overlay)
        self.client = Mock()
        self.client.query.side_effect = self.query
        self.client_type = Mock(return_value=self.client)
        self.fcntl = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {
            'pynq': SimpleNamespace(PL=self.pl, Clocks=self.clocks,
                                    Overlay=self.overlay, MMIO=self.mmio),
            'fcntl': self.fcntl,
            'board_pointcloud_export': SimpleNamespace(board_identity=lambda: {
                'is_linux_arm_zynq_or_pynq': True}),
            'motor_uartlite': SimpleNamespace(UARTLiteReadOnlyClient=self.client_type),
            'object_board_scan': SimpleNamespace(assert_idle=assert_idle)}))
        self.stack.enter_context(patch('builtins.open', mock_open()))
        self.fuser = self.stack.enter_context(patch.object(loader.subprocess, 'run',
            return_value=SimpleNamespace(returncode=1, stdout='')))
        self.processes = self.stack.enter_context(patch.object(Path, 'glob', return_value=[]))
        self.stack.enter_context(patch.object(loader.time, 'sleep'))
        self.stack.enter_context(patch.object(loader.os, 'environ', {}))
        self.stack.enter_context(patch.object(sys, 'path', sys.path.copy()))

    def make_overlay(self, path, download=True):
        if download:
            self.pl.bitfile_name = str(path)
        return SimpleNamespace()

    @staticmethod
    def query(name, **kwargs):
        return {'configuration': {'firmware_family_layout': 'mock'},
                'status': {'position_reached': True, 'stall_detected': False,
                           'stall_protection_triggered': False},
                'position': {'motor_position_deg': 0.0}}[name]

    def run_loader(self):
        return loader.load_board(self.config, self.artifacts)

    def assert_no_download(self):
        self.assertFalse(any(call.kwargs.get('download', True)
                             for call in self.overlay.call_args_list))

    def test_existing_button_attaches_without_uart_or_download(self):
        self.client_type.side_effect = AssertionError('UART must not be queried on attach')
        report = self.run_loader()
        self.assertEqual(report['status'], 'button_overlay_already_loaded')
        self.assertEqual(report['previous']['idle_diagnostics']['completed_frames'], 56)
        self.assertEqual(report['previous']['motor_uart_check'],
                         'not_required_without_reprogramming')
        self.assertFalse(report['motor_motion_sent'])
        self.assertEqual(report['uart_startup_status'], [])
        self.overlay.assert_not_called()
        self.client_type.assert_not_called()
        self.uart.read.assert_not_called()
        self.assertEqual(self.fcntl.flock.call_count, 2)
        self.fuser.assert_called_once_with(['fuser', '/dev/video0'],
                                          capture_output=True, text=True, timeout=5)
        self.core.write.assert_not_called()
        self.dma.write.assert_not_called()

    def test_existing_button_requires_matching_file_hash(self):
        self.artifacts['button_release']['laser_motor_button.bit']['sha256'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'differs from selected release'):
            self.run_loader()
        self.client_type.assert_not_called()
        self.assert_no_download()

    def test_existing_button_rejects_core_and_dma_busy_or_error(self):
        for target, offset, value in ((self.core_values, 4, 1),
                                      (self.core_values, 4, 4),
                                      (self.dma_values, 4, 0),
                                      (self.dma_values, 0x34, 0x12)):
            with self.subTest(offset=offset, value=value):
                original = target[offset]
                target[offset] = value
                try:
                    with self.assertRaises(RuntimeError):
                        self.run_loader()
                finally:
                    target[offset] = original
        self.client_type.assert_not_called()
        self.assert_no_download()

    def test_existing_button_requires_clock_core_id_and_version(self):
        self.clocks.fclk0_mhz = 100.0
        with self.assertRaisesRegex(RuntimeError, 'identity/clock'):
            self.run_loader()
        self.clocks.fclk0_mhz = 50.0
        for offset in (8, 12):
            original = self.core_values[offset]
            self.core_values[offset] = 0
            try:
                with self.assertRaisesRegex(RuntimeError, 'ID/version/idle'):
                    self.run_loader()
            finally:
                self.core_values[offset] = original
        self.client_type.assert_not_called()
        self.assert_no_download()

    def test_camera_occupation_blocks_attach(self):
        self.fuser.return_value = SimpleNamespace(returncode=0, stdout='123')
        with self.assertRaisesRegex(RuntimeError, 'Camera is occupied'):
            self.run_loader()
        self.mmio.assert_not_called()
        self.client_type.assert_not_called()

    def test_both_exclusive_locks_are_required(self):
        for side_effect in ([BlockingIOError('service occupied')],
                            [None, BlockingIOError('scan occupied')]):
            with self.subTest(side_effect=side_effect):
                self.fcntl.flock.side_effect = side_effect
                with self.assertRaises(BlockingIOError):
                    self.run_loader()
        self.mmio.assert_not_called()
        self.client_type.assert_not_called()

    def test_other_hardware_process_blocks_attach(self):
        process = Mock()
        process.name = str(loader.os.getpid() + 100000)
        process.joinpath.return_value.read_bytes.return_value = b'python\0object_board_scan.py\0'
        self.processes.return_value = [process]
        with self.assertRaisesRegex(RuntimeError, 'Another hardware job'):
            self.run_loader()
        self.mmio.assert_not_called()
        self.client_type.assert_not_called()

    def test_unknown_overlay_is_rejected(self):
        self.pl.bitfile_name = str(self.project / 'unknown.bit')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected configured overlay'):
            self.run_loader()
        self.mmio.assert_not_called()
        self.client_type.assert_not_called()

    def test_integrated_transition_keeps_stationary_uart_checks(self):
        self.pl.bitfile_name = str(self.integrated)
        report = self.run_loader()
        self.assertEqual(report['status'], 'button_overlay_loaded')
        self.assertEqual(report['previous']['motor_positions'], [0.0, 0.0, 0.0])
        self.assertEqual([call.args[0] for call in self.client.query.call_args_list],
                         ['configuration', 'status', 'position', 'status', 'position', 'status', 'position'])
        self.client.close.assert_called_once_with()
        self.assertEqual(self.overlay.call_args_list[0].args, (str(self.integrated),))
        self.assertEqual(self.overlay.call_args_list[0].kwargs, {'download': False})
        self.assertEqual(self.overlay.call_args_list[1].args, (str(self.button),))
        self.assertEqual(report['uart_startup_status'], [0x40, 4, 4, 4])
        self.uart.write.assert_not_called()

    def test_integrated_uart_error_still_blocks_programming(self):
        self.pl.bitfile_name = str(self.integrated)
        self.client.query.side_effect = RuntimeError('UART frame error 0x40')
        with self.assertRaisesRegex(RuntimeError, 'UART frame error'):
            self.run_loader()
        self.client.close.assert_called_once_with()
        self.assert_no_download()
        self.uart.read.assert_not_called()

    def test_integrated_moving_motor_still_blocks_programming(self):
        self.pl.bitfile_name = str(self.integrated)
        self.client.query.side_effect = lambda name, **kwargs: (
            {'position_reached': False, 'stall_detected': False, 'stall_protection_triggered': False}
            if name == 'status' else self.query(name, **kwargs))
        with self.assertRaisesRegex(RuntimeError, 'Motor is not stopped'):
            self.run_loader()
        self.client.close.assert_called_once_with()
        self.assert_no_download()

    def test_cold_start_keeps_loading_and_startup_uart_observation(self):
        self.pl.bitfile_name = None
        report = self.run_loader()
        self.assertEqual(report['previous'], {
            'previous_configuration': 'unconfigured_after_boot', 'registers_read': False})
        self.overlay.assert_called_once_with(str(self.button))
        self.client_type.assert_not_called()
        self.assertEqual(report['uart_startup_status'], [0x40, 4, 4, 4])
        self.uart.write.assert_not_called()

    def test_new_configuration_requires_three_quiet_empty_uart_samples(self):
        for status in ([4, 0x40, 4, 4], [4, 4, 0x40, 4], [4, 4, 4, 0x40],
                       [4, 0, 4, 4], [4, 5, 4, 4]):
            with self.subTest(status=status):
                self.pl.bitfile_name = None
                self.uart.read.side_effect = status
                with self.assertRaisesRegex(RuntimeError, 'New UART did not settle'):
                    self.run_loader()
        self.client_type.assert_not_called()
        self.uart.write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
