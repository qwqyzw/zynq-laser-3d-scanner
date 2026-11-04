"""Offline integrity/ownership tests for the new integrated release entry.

Creates temporary bit/HWH fixtures and fake PYNQ/MMIO objects. Never opens
devices, downloads an actual overlay, allocates DMA buffers or moves a motor.
The existing frozen-static entry checker remains separate and unchanged.
"""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '04_runtime'))
import laser_stripe
import pl_runtime
import run_integrated_board as board_runner


class IntegratedEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='integrated_entry_offline_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        files = (
            '06_fpga_verified/SHA256.json', '06_fpga_verified/laser_static.bit',
            '06_fpga_verified/laser_static.hwh', '06_fpga_verified/software/laser_stripe.py',
            '04_runtime/laser_stripe.py', '11_fpga_project/software/laser_stripe.py',
            '11_fpga_project/rtl/laser_stripe_core.sv',
            '11_fpga_project/rtl/laser_stripe_axi.sv',
            '04_runtime/motor_uartlite.py',
        )
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        self.release = self.root / '14_integrated_release_20261020'
        self.release.mkdir()
        self.manifest = {'files': [], 'source_sha256': {}}
        for name in ('laser_motor.bit', 'laser_motor.hwh'):
            content = ('OFFLINE_FIXTURE_ONLY:' + name).encode()
            (self.release / name).write_bytes(content)
            self.manifest['files'].append({'path': name, 'bytes': len(content),
                                          'sha256': hashlib.sha256(content).hexdigest()})
        for name in ('11_fpga_project/rtl/laser_stripe_core.sv',
                     '11_fpga_project/rtl/laser_stripe_axi.sv',
                     '04_runtime/laser_stripe.py', '04_runtime/motor_uartlite.py'):
            self.manifest['source_sha256'][name] = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
        self.write_manifest()
        self.calls = {'overlays': [], 'mmio': [], 'writes': [], 'allocations': 0}
        self.identity = {laser_stripe.ID: laser_stripe.IP_ID,
                         laser_stripe.VERSION: laser_stripe.IP_VERSION,
                         laser_stripe.REG_INPUT_BYTES: laser_stripe.INPUT_BYTES,
                         laser_stripe.REG_OUTPUT_BYTES: laser_stripe.OUTPUT_BYTES,
                         laser_stripe.REG_WIDTH: laser_stripe.WIDTH,
                         laser_stripe.REG_HEIGHT: laser_stripe.HEIGHT}
        outer = self

        class FakeOverlay:
            def __init__(self, path):
                outer.calls['overlays'].append(path)
                self.ip_dict = {
                    'laser_stripe_0': {'phys_addr': 0x43C00000, 'addr_range': 65536},
                    'axi_dma_0': {'phys_addr': 0x40400000, 'addr_range': 65536,
                                  'parameters': {'C_INCLUDE_SG': '0', 'C_SG_LENGTH_WIDTH': '23',
                                                 'C_ADDR_WIDTH': '32', 'C_INCLUDE_MM2S': '1',
                                                 'C_INCLUDE_S2MM': '1'}},
                    'axi_uartlite_0': {'phys_addr': 0x42C00000, 'addr_range': 65536},
                }

            def __getattr__(self, name):
                if name in ('axi_dma_0', 'axi_uartlite_0', 'iop_arduino'):
                    raise AssertionError('Native DMA/UART must not be constructed by PL entry')
                raise AttributeError(name)

        class FakeMMIO:
            def __init__(self, address, length):
                self.address = address
                outer.calls['mmio'].append((address, length))

            def read(self, offset):
                if self.address != 0x43C00000:
                    raise AssertionError('Driver binding must not read DMA/UART')
                return outer.identity[offset]

            def write(self, offset, value):
                outer.calls['writes'].append((offset, value))
                raise AssertionError('Driver binding must not write hardware')

        def allocate(**kwargs):
            outer.calls['allocations'] += 1
            raise AssertionError('Entry must not allocate DMA memory')

        self.fake = ModuleType('pynq')
        self.fake.Overlay = FakeOverlay
        self.fake.MMIO = FakeMMIO
        self.fake.allocate = allocate
        self.old_active = pl_runtime._active_runtime
        pl_runtime._active_runtime = None
        self.addCleanup(setattr, pl_runtime, '_active_runtime', self.old_active)

    def write_manifest(self):
        (self.release / 'SHA256.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def load_fake(self):
        with patch.object(pl_runtime, 'PROJECT_ROOT', self.root), \
             patch.object(pl_runtime._driver_module, '__file__', str(self.root / '04_runtime/laser_stripe.py')), \
             patch.dict(sys.modules, {'pynq': self.fake}):
            return pl_runtime.load_integrated_runtime()

    def test_inspection_without_pynq_and_static_stays_distinct(self):
        with patch.dict(sys.modules, {'pynq': None}):
            report = pl_runtime.inspect_integrated_artifacts(self.root)
            static = pl_runtime.inspect_artifacts(self.root)
        self.assertFalse(report['hardware_accessed'])
        self.assertTrue(report['motor_uart_available'])
        self.assertFalse(static['motor_uart_available'])
        self.assertEqual(set(report['integrated_release']), {'laser_motor.bit', 'laser_motor.hwh'})

    def test_one_explicit_download_no_DMA_or_UART_construction(self):
        runtime = self.load_fake()
        self.assertIsInstance(runtime, pl_runtime.IntegratedPLRuntime)
        self.assertIsInstance(runtime.driver, laser_stripe.LaserStripe)
        self.assertTrue(runtime.motor_uart_available)
        self.assertEqual(self.calls['overlays'], [str(self.release / 'laser_motor.bit')])
        self.assertEqual(self.calls['mmio'], [(0x40400000, 65536), (0x43C00000, 65536)])
        self.assertEqual(self.calls['writes'], [])
        self.assertEqual(self.calls['allocations'], 0)
        with self.assertRaisesRegex(pl_runtime.IntegrationError, 'already exists'):
            self.load_fake()
        with self.assertRaisesRegex(pl_runtime.IntegrationError, 'already exists'):
            pl_runtime.load_static_runtime()
        self.assertEqual(len(self.calls['overlays']), 1)

    def test_prior_static_owner_blocks_new_programming(self):
        pl_runtime._active_runtime = object()
        with self.assertRaisesRegex(pl_runtime.IntegrationError, 'already exists'):
            self.load_fake()
        self.assertEqual(self.calls['overlays'], [])

    def test_each_paired_file_hash_is_checked_before_download(self):
        for name in ('laser_motor.bit', 'laser_motor.hwh'):
            path = self.release / name
            original = path.read_bytes()
            path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            with self.subTest(file=name), self.assertRaisesRegex(pl_runtime.IntegrationError, 'hash differs'):
                self.load_fake()
            path.write_bytes(original)
        self.assertEqual(self.calls['overlays'], [])

    def test_missing_paired_file_blocks_before_download(self):
        (self.release / 'laser_motor.hwh').unlink()
        with self.assertRaises(pl_runtime.IntegrationError):
            self.load_fake()
        self.assertEqual(self.calls['overlays'], [])

    def test_manifest_missing_pair_entry_blocks_before_download(self):
        self.manifest['files'] = self.manifest['files'][:1]
        self.write_manifest()
        with self.assertRaisesRegex(pl_runtime.IntegrationError, 'Missing integrated artifact'):
            self.load_fake()
        self.assertEqual(self.calls['overlays'], [])

    def test_source_drift_in_core_wrapper_or_UART_blocks_download(self):
        for name in ('11_fpga_project/rtl/laser_stripe_core.sv',
                     '11_fpga_project/rtl/laser_stripe_axi.sv', '04_runtime/motor_uartlite.py'):
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n# offline simulated drift\n')
            with self.subTest(source=name), self.assertRaisesRegex(pl_runtime.IntegrationError, 'source differs'):
                self.load_fake()
            path.write_bytes(original)
        self.assertEqual(self.calls['overlays'], [])

    def test_missing_core_source_binding_blocks_download(self):
        del self.manifest['source_sha256']['11_fpga_project/rtl/laser_stripe_core.sv']
        self.write_manifest()
        with self.assertRaisesRegex(pl_runtime.IntegrationError, 'Missing source binding'):
            self.load_fake()
        self.assertEqual(self.calls['overlays'], [])

    def test_release_traversal_and_duplicate_paths_block_download(self):
        for entry in ({'path': '../../outside.bit', 'bytes': 1, 'sha256': 'x'},
                      dict(self.manifest['files'][0])):
            self.manifest['files'].append(entry)
            self.write_manifest()
            with self.subTest(path=entry['path']), self.assertRaisesRegex(pl_runtime.IntegrationError, 'manifest path'):
                self.load_fake()
            self.manifest['files'].pop()
        self.assertEqual(self.calls['overlays'], [])

    def test_source_traversal_blocks_download(self):
        self.manifest['source_sha256']['../outside.py'] = 'ignored'
        self.write_manifest()
        with self.assertRaisesRegex(pl_runtime.IntegrationError, 'source differs'):
            self.load_fake()
        self.assertEqual(self.calls['overlays'], [])

    def test_wrong_imported_driver_blocks_before_pynq(self):
        with patch.object(pl_runtime, 'PROJECT_ROOT', self.root), \
             patch.dict(sys.modules, {'pynq': None}), \
             self.assertRaisesRegex(pl_runtime.IntegrationError, 'another project'):
            pl_runtime.load_integrated_runtime()
        self.assertEqual(self.calls['overlays'], [])

    def test_bad_timeout_blocks_before_pynq(self):
        for value in (0, -1, float('inf'), float('nan'), True, '10'):
            with self.subTest(timeout=value), patch.dict(sys.modules, {'pynq': None}), \
                 self.assertRaises(ValueError):
                pl_runtime.load_integrated_runtime(timeout=value)
        self.assertEqual(self.calls['overlays'], [])


class RecoveryOwnershipTests(unittest.TestCase):
    class Driver:
        def __init__(self):
            self.healthy = False
            self.quarantined_buffer_count = 2
            self.recover_calls = 0
            self.diagnostic_calls = 0

        def recover(self):
            self.recover_calls += 1
            self.healthy = True
            self.quarantined_buffer_count = 0

        def diagnostics(self):
            self.diagnostic_calls += 1
            raise OSError('offline simulated MMIO diagnostic failure')

    def test_hold_survives_log_input_and_sleep_failures_until_recovery(self):
        driver = self.Driver()
        report = {}
        with patch.object(board_runner, 'save', side_effect=OSError('disk failed')), \
             patch('builtins.print', side_effect=BrokenPipeError('output disconnected')), \
             patch('builtins.input', side_effect=['invalid', EOFError(), KeyboardInterrupt(), 'recover']) as inputs, \
             patch.object(board_runner.time, 'sleep', side_effect=KeyboardInterrupt()):
            board_runner.retain_until_quiescent(SimpleNamespace(driver=driver), Path('unused.json'), report)
        self.assertEqual(inputs.call_count, 4)
        self.assertEqual(driver.recover_calls, 1)
        self.assertEqual(driver.quarantined_buffer_count, 0)
        self.assertTrue(report['recovery_hold'])
        self.assertTrue(report['recovery_completed'])
        self.assertIn('report_write_error', report)
        self.assertIn('diagnostics_after_recovery_error', report)

    def test_execute_finally_reaches_hold_despite_all_reporting_failures(self):
        driver = self.Driver()
        runtime = SimpleNamespace(driver=driver, overlay=object())
        original_read = Path.read_bytes
        base_queries = []
        metadata_downloads = []

        class OldReadOnly:
            transactions = []

            def __init__(self, *args, **kwargs):
                pass

            def query(self, name, **kwargs):
                base_queries.append(name)
                if name == 'status':
                    return {'position_reached': True, 'stall_detected': False,
                            'stall_protection_triggered': False}
                return {'motor_position_deg': 0.0}

            def close(self):
                pass

        class NewReadOnly(OldReadOnly):
            def query(self, name, **kwargs):
                raise RuntimeError('offline injected failure after acquiring runtime')

        def mocked_read(path):
            if path.as_posix().endswith('/sys/firmware/devicetree/base/chosen/pynq_board'):
                return b'Pynq-Z2\0'
            return original_read(path)

        def overlay(path, *, download):
            metadata_downloads.append(download)
            return SimpleNamespace(is_loaded=lambda: True)

        with tempfile.TemporaryDirectory(prefix='recovery_owner_offline_') as temp:
            root = Path(temp)
            (root / '04_runtime').mkdir()
            base = root / '06_fpga_verified/base_recovery/base.bit'
            base.parent.mkdir(parents=True)
            base.write_bytes(b'OFFLINE_BASE_FIXTURE_ONLY')
            fake_pynq = ModuleType('pynq')
            fake_pynq.Overlay = overlay
            fake_pynq.PL = SimpleNamespace(bitfile_name=str(base), timestamp='fixture_timestamp')
            fake_pynq.Clocks = SimpleNamespace(fclk0_mhz=50.0)
            fake_old = ModuleType('motor_arduino')
            fake_old.ArduinoReadOnlyClient = OldReadOnly
            fake_new = ModuleType('motor_uartlite')
            fake_new.UARTLiteReadOnlyClient = NewReadOnly
            with patch.object(board_runner, 'ROOT', root), \
                 patch.dict(board_runner.os.environ, {}), \
                 patch.object(Path, 'read_bytes', mocked_read), \
                 patch.object(board_runner.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='')), \
                 patch.object(board_runner, 'load_integrated_runtime', return_value=runtime), \
                 patch.dict(sys.modules, {'pynq': fake_pynq, 'motor_arduino': fake_old,
                                          'motor_uartlite': fake_new}), \
                 patch.object(board_runner.time, 'sleep', return_value=None), \
                 patch.object(board_runner, 'save', side_effect=OSError('disk failed')), \
                 patch('builtins.print', side_effect=BrokenPipeError('output disconnected')), \
                 patch('builtins.input', return_value='recover') as inputs:
                report = board_runner.execute(root / 'session', client_date='2026-10-20',
                                              roi=(175, 340, 400, 400), exposure=5)
        self.assertEqual(metadata_downloads, [False])
        self.assertEqual(base_queries, ['status', 'position'] * 3)
        self.assertEqual(inputs.call_count, 1)
        self.assertEqual(driver.recover_calls, 1)
        self.assertGreaterEqual(driver.diagnostic_calls, 2)
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['recovery_completed'])
        self.assertIn('diagnostics_error', report)
        self.assertIn('report_write_error', report)


def main():
    suite = unittest.TestSuite()
    for case in (IntegratedEntryTests, RecoveryOwnershipTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'offline_only': True, 'current_hardware_accessed': False,
                      'fake_paired_artifacts': True, 'tests_run': result.testsRun,
                      'all_passed': result.wasSuccessful()}, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
