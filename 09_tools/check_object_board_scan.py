"""Offline injected-object checks. All generated sessions are explicitly simulated."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import object_board_scan as scan

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '04_runtime'))
from motor_position import MotorGeometry, plan_relative_move


class FakeDriver:
    def __init__(self):
        self.count = 68
        self.healthy = True
        self.quarantined_buffer_count = 0

    def diagnostics(self):
        return {'status': 2, 'completed_frames': self.count,
                'quarantined_buffers': self.quarantined_buffer_count,
                'dma': {'mm2s_cr': 1, 'mm2s_sr': 0x1002, 's2mm_cr': 1,
                        's2mm_sr': 0x1002, 'received_bytes': 2560}}


class FakeMotorBase:
    def __init__(self, trace):
        self.trace = trace
        self.prepared = False
        self.closed = False
        self.geometry = MotorGeometry(1.8, 16)
        self.value = -.0494384765625
        self.motion_log = []
        self.transactions = []
        self.targets = []
        self.prepare_calls = 0
        self.fail_move = None

    def _exchange(self, operation, plan=None):
        self.trace.append(('FD' if operation == 2 else 'stop', copy.deepcopy(plan)))
        row = {'operation': operation, 'plan': copy.deepcopy(plan)}
        self.motion_log.append(row)
        return row

    def prepare(self):
        self.trace.append(('prepare', None))
        self.prepare_calls += 1
        self.stop()
        self.prepared = True
        return {'initial_position_deg': self.value,
                'initial_settle': {'motor_position_deg': self.value},
                'backend': 'OFFLINE_FAKE_MOTOR'}

    def status(self):
        self.transactions.append({'query': 'status', 'simulation': True})
        return {'enabled': True, 'position_reached': True,
                'stall_detected': False, 'stall_protection_triggered': False}

    def position(self):
        self.transactions.append({'query': 'position', 'simulation': True})
        return {'motor_position_deg': self.value}

    def move_to(self, target, rpm=1):
        plan = plan_relative_move(target - self.value, self.geometry, rpm)
        plan.update(requested_target_motor_deg=target,
                    quantized_target_motor_deg=self.value + plan['quantized_delta_deg'])
        self.targets.append(target)
        if plan['pulses']:
            self._exchange(2, plan)
        if len(self.targets) == self.fail_move:
            raise TimeoutError('INJECTED motor positioning failure')
        self.value = plan['quantized_target_motor_deg']
        return {'plan': plan, 'motor_position_deg': self.value,
                'settled': {'motor_position_deg': self.value}}

    def stop(self):
        return self._exchange(0)

    def close(self):
        self.stop()
        self.closed = True


class FakeMotor(scan.FiniteEnvelopeMixin, FakeMotorBase):
    pass


class FakeCamera:
    def __init__(self, trace, driver):
        self.trace, self.driver = trace, driver
        self.calls = 0
        self.fail_capture = None
        self.on_capture = None
        self.proc = None
        self.cleanup = {}

    def start(self, root):
        self.trace.append(('camera_start', None))
        return {'source': 'OFFLINE_FAKE_CAMERA'}

    def capture(self, folder):
        self.calls += 1
        self.trace.append(('capture', self.calls))
        folder.mkdir(parents=True, exist_ok=False)
        if self.calls == self.fail_capture:
            raise RuntimeError('INJECTED camera or quality failure')
        images, rows = [], []
        for frame in range(3):
            # Deliberately not a PNG and retained only under simulated checks.
            file = folder / ('raw_%02d.mock' % frame)
            file.write_bytes(('OFFLINE_SIMULATION_%d_%d' % (self.calls, frame)).encode())
            images.append({'file': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
            self.driver.count += 1
            rows.append({'frame': frame, 'completed_frames': self.driver.count,
                         'valid_equal': True, 'max_pixel_difference': 0})
            (folder / ('pl_%02d_actual.bin' % frame)).write_bytes(bytes(2560))
        (folder / 'pl_transactions.json').write_text(json.dumps(rows), encoding='utf-8')
        if self.on_capture:
            self.on_capture(self.calls)
        return {'source': 'hardware_camera', 'stripe_coordinate_source': 'supplied_PL_driver',
                'images': images, 'stripe_quality': {
                    'image_quality_gate_passed': True, 'common_quality_columns': 100}}

    def close(self):
        self.trace.append(('camera_close', None))
        return {'camera_release_confirmed': True, 'exposure_restoration_verified': True,
                'no_active_capture_process': True, 'capture_count': self.calls}


class ObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = ROOT / 'results/object_scan_offline_checks'
        cls.base.mkdir(parents=True, exist_ok=True)
        (cls.base / 'README.txt').write_text(
            'OFFLINE FAKE OBJECTS ONLY. No board or real images. These are not hardware evidence.\n',
            encoding='utf-8')

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '_', dir=self.base)).resolve()
        self.trace = []
        self.driver = FakeDriver()
        self.motor = FakeMotor(self.trace)
        self.camera = FakeCamera(self.trace, self.driver)
        self.identity = {'bitfile': 'OFFLINE_SIMULATION.bit', 'sha256': 'FAKE',
                         'timestamp': 'FAKE', 'fclk0_mhz': 50}
        self.runtime = SimpleNamespace(driver=self.driver, identity=self.identity,
                                       initial_diagnostics=self.driver.diagnostics(),
                                       read_identity=lambda: self.identity.copy())

    def run_case(self, angle=10):
        with patch.object(scan, 'progress'), patch.object(scan.time, 'sleep'):
            return scan.run_acquisition(self.root, self.runtime, self.motor, self.camera,
                                        scan.nominal_plan(angle), client_date='2026-10-20',
                                        roi=(175, 340, 400, 400), exposure=5, hardware=False)

    def test_complete_route_single_origin_and_baseline_counter(self):
        report = self.run_case()
        self.assertEqual(report['status'], 'completed')
        self.assertTrue(report['simulation'])
        self.assertFalse(report['hardware_accessed'])
        self.assertFalse(report['real_hardware_acquisition_verified'])
        self.assertEqual(self.motor.prepare_calls, 1)
        origin = report['global_origin_motor_deg']
        self.assertEqual([round(value - origin, 6) for value in self.motor.targets], [5, 10, 5, 0])
        self.assertEqual(self.camera.calls, 5)
        self.assertEqual(report['baseline_completed_frames'], 68)
        self.assertEqual(report['diagnostics']['completed_frames'], 83)
        self.assertEqual(report['fd_attempts'], 4)
        self.assertTrue(report['return_to_origin_completed'])
        self.assertIn('return_origin_capture', report)
        self.assertLess(self.trace.index(('capture', 1)), self.trace.index(('prepare', None)))

    def test_preflight_failure_precedes_motor_prepare(self):
        self.camera.fail_capture = 1
        report = self.run_case()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(self.motor.prepare_calls, 0)
        self.assertEqual(self.motor.targets, [])
        self.assertTrue(report['cleanup']['motor_stop_confirmed'])

    def test_initial_motor_motion_prevents_camera_and_prepare(self):
        with patch.object(self.motor, 'status', return_value={
                'enabled': True, 'position_reached': False,
                'stall_detected': False, 'stall_protection_triggered': False}):
            report = self.run_case()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(self.camera.calls, 0)
        self.assertEqual(self.motor.prepare_calls, 0)
        self.assertEqual(self.motor.targets, [])

    def test_quality_failure_stops_without_return(self):
        self.camera.fail_capture = 3
        report = self.run_case()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(self.motor.targets), 1)
        self.assertEqual(report['return_points'], [])
        self.assertFalse(report['automatic_return_after_failure'])
        self.assertEqual(report['points'][-1]['status'], 'failed')

    def test_abort_before_start_sends_no_move(self):
        (self.root / 'abort.request').write_text('stop', encoding='utf-8')
        report = self.run_case()
        self.assertEqual(report['status'], 'aborted')
        self.assertEqual(self.motor.prepare_calls, 0)
        self.assertEqual(self.motor.targets, [])

    def test_abort_after_capture_never_starts_return(self):
        def abort_after_first_pose(number):
            if number == 2:
                (self.root / 'abort.request').write_text('stop', encoding='utf-8')
        self.camera.on_capture = abort_after_first_pose
        report = self.run_case()
        self.assertEqual(report['status'], 'aborted')
        self.assertEqual(self.motor.targets, [])
        self.assertEqual(report['return_points'], [])

    def test_movement_failure_never_automatically_returns(self):
        self.motor.fail_move = 1
        report = self.run_case()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(self.motor.targets), 1)
        self.assertEqual(report['return_points'], [])
        self.assertTrue(self.motor.closed)

    def test_return_failure_does_not_attempt_remaining_waypoints(self):
        self.motor.fail_move = 3
        report = self.run_case()
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['acquisition_completed'])
        self.assertEqual(len(self.motor.targets), 3)
        self.assertEqual(len(report['return_points']), 1)
        self.assertEqual(report['return_points'][0]['status'], 'failed')
        self.assertNotIn('return_to_origin_completed', report)

    def test_camera_cleanup_failure_cannot_claim_success(self):
        with patch.object(self.camera, 'close', side_effect=RuntimeError('INJECTED restoration uncertainty')):
            report = self.run_case()
        self.assertEqual(report['status'], 'cleanup_failed')
        self.assertTrue(report['cleanup']['motor_stop_confirmed'])
        self.assertFalse(report['real_hardware_acquisition_verified'])

    def test_dma_quarantine_preserved_on_failure(self):
        def lose_dma(number):
            if number == 2:
                self.driver.healthy = False
                self.driver.quarantined_buffer_count = 2
        self.camera.on_capture = lose_dma
        report = self.run_case()
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['driver_healthy'])
        self.assertEqual(report['quarantined_buffers'], 2)
        self.assertEqual(self.driver.quarantined_buffer_count, 2)
        self.assertTrue(report['cleanup']['motor_stop_confirmed'])

    def test_overlay_change_is_caught_before_prepare(self):
        self.camera.on_capture = lambda _: self.identity.update(timestamp='CHANGED')
        # Keep the initial identity immutable, as the real runtime does.
        original = self.runtime.identity.copy()
        self.runtime.identity = original
        report = self.run_case()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(self.motor.prepare_calls, 0)

    def test_frame_counter_jump_rejects_capture(self):
        self.camera.on_capture = lambda _: setattr(self.driver, 'count', self.driver.count + 1)
        report = self.run_case()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(self.motor.prepare_calls, 0)

    def test_checkpoint_failure_still_stops(self):
        original_save = scan.save
        def fail_mid_move(path, data):
            if path.name == 'completion.json' and data.get('points'):
                raise OSError('INJECTED full disk')
            return original_save(path, data)
        with patch.object(scan, 'save', side_effect=fail_mid_move):
            report = self.run_case()
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['cleanup']['motor_stop_confirmed'])
        self.assertTrue(self.motor.closed)
        self.assertEqual(report['return_points'], [])

    def test_180_degree_plan_includes_return_recapture(self):
        plan = scan.nominal_plan(180)
        self.assertEqual(plan['expected_capture_batches'], 39)
        self.assertEqual(plan['expected_new_PL_transactions'], 117)
        self.assertEqual(plan['expected_original_images'], 351)
        self.assertEqual(plan['maximum_FD_attempts'], 72)

    def test_full_180_fake_session_binds_three_stages_and_returns(self):
        report = self.run_case(180)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(len(report['points']), 37)
        self.assertEqual(len(report['return_points']), 36)
        self.assertEqual(len(report['bound_stage_records']), 3)
        self.assertEqual(report['diagnostics']['completed_frames'], 185)
        self.assertEqual(report['fd_attempts'], 72)
        self.assertEqual(self.camera.calls, 39)
        self.assertEqual(self.motor.prepare_calls, 1)
        self.assertTrue(all(point['global_origin_motor_deg'] == report['global_origin_motor_deg']
                            for point in report['points']))
        self.assertLess(abs(report['return_origin_capture']['feedback_relative_deg']), .1)

    def armed_plan(self, delta=5):
        self.motor.prepare()
        self.motor.arm_object_envelope(self.motor.value, 10)
        plan = plan_relative_move(delta, self.motor.geometry, rpm=1)
        plan.update(requested_target_motor_deg=self.motor.value + delta,
                    quantized_target_motor_deg=self.motor.value + plan['quantized_delta_deg'])
        return plan

    def test_guard_rejects_large_step_before_transmit(self):
        plan = self.armed_plan(10)
        with self.assertRaisesRegex(RuntimeError, '5.5'):
            self.motor._exchange(2, plan)
        self.assertEqual(self.motor.fd_attempts, 0)

    def test_guard_binds_transmitted_pulses_and_speed(self):
        plan = self.armed_plan()
        for altered in (dict(plan, pulses=100), dict(plan, rpm=2)):
            with self.subTest(plan=altered), self.assertRaisesRegex(RuntimeError, 'geometry or speed'):
                self.motor._exchange(2, altered)
        self.assertEqual(self.motor.fd_attempts, 0)

    def test_guard_rejects_origin_reset_and_outside_target(self):
        plan = self.armed_plan()
        with self.assertRaisesRegex(RuntimeError, 'cannot be changed'):
            self.motor.arm_object_envelope(60, 10)
        plan['quantized_target_motor_deg'] = self.motor.object_origin + 11
        with self.assertRaisesRegex(RuntimeError, 'global feedback'):
            self.motor._exchange(2, plan)

    def test_guard_budget_and_stop_remains_available(self):
        plan = self.armed_plan()
        self.motor.fd_attempts = self.motor.fd_limit
        with self.assertRaisesRegex(RuntimeError, 'budget'):
            self.motor._exchange(2, plan)
        self.motor.stop()
        self.assertEqual(self.motor.motion_log[-1]['operation'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
