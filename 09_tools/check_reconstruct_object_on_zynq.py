"""Isolated synthetic math and rejection checks; never exports a real model."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import reconstruct_object_on_zynq as reconstruct

PROJECT = Path(__file__).resolve().parents[1]
MODS = reconstruct.modules(PROJECT)
BASE = PROJECT / 'results/object_reconstruction_offline_checks'


def model():
    return {
        'shape_preview_candidate_usable': True, 'metric_accuracy_accepted': False,
        'object_used_in_geometry_fit': False, 'historical_extrinsics_used': False,
        'execution_role': 'development', 'synthetic': True,
        'camera': {'K': [[100, 0, 320], [0, 100, 240], [0, 0, 1]],
                   'dist_coeffs': [0, 0, 0, 0, 0], 'image_size': [640, 480]},
        'laser_plane': {'normal': [0, 0, 1], 'offset_mm': -100},
        'reference_plane': {'normal': [0, 0, -1], 'offset_mm': 110},
        'axis': {'candidate_checks_passed': True, 'motor_zero_reference_deg': 30,
                 'axis_direction_camera': [0, 0, 1],
                 'axis_point_on_initial_paper_mm': [0, 0, 100],
                 'turntable_basis_camera_columns': np.eye(3).tolist(),
                 'large_angle_axis_validation_passed': False}}


class MathAndEvidenceChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BASE.mkdir(parents=True, exist_ok=True)
        (BASE / 'README.txt').write_text(
            'SYNTHETIC MATHEMATICS AND ISOLATED TAMPER CHECKS ONLY.\n'
            'No board connection, no real XYZ reconstruction/export, no hardware acceptance.\n'
            'Historical image copies are used only for input tamper rejection, never 3D.\n',
            encoding='utf-8')

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '_', dir=BASE))

    def test_absolute_motor_zero_and_undo_direction(self):
        result = reconstruct.reconstruct_profile(np.array([[330., 240.]]), 120, model(), MODS)
        np.testing.assert_allclose(result['camera_xyz'], [[10, 0, 100]], atol=1e-12)
        np.testing.assert_allclose(result['table_xyz'], [[0, -10, 0]], atol=1e-12)
        self.assertEqual(result['angle_deg'], 90)
        self.assertTrue(result['selected'][0])

    def test_circle_profiles_undo_to_same_point_without_added_vertices(self):
        # Analytic circle in z=100 projected by K; not a camera/PL fixture.
        initial = np.array([12., 8.])
        outputs = []
        for deg in (0, 5, 60, 120, 180):
            angle = np.radians(deg)
            xy = np.array([[np.cos(angle), -np.sin(angle)],
                           [np.sin(angle), np.cos(angle)]]) @ initial
            result = reconstruct.reconstruct_profile(np.array([[320 + xy[0], 240 + xy[1]]]),
                                                       deg + 30, model(), MODS)
            outputs.append(result['table_xyz'][0])
        np.testing.assert_allclose(outputs, np.tile([12, 8, 0], (5, 1)), atol=1e-12)

    def test_tilted_axis_offset_forward_inverse(self):
        axis = model()['axis']
        direction = np.array([0., .6, .8])
        basis = np.column_stack(([1., 0, 0], np.cross(direction, [1, 0, 0]), direction))
        centre = np.array([8., -4., 120.])
        axis.update(axis_direction_camera=direction.tolist(),
                    axis_point_on_initial_paper_mm=centre.tolist(),
                    turntable_basis_camera_columns=basis.tolist())
        expected = np.array([[7., 13., 11.], [-6., 4., 8.]])
        a = np.radians(37.)
        cross = np.array([[0, -direction[2], direction[1]],
                          [direction[2], 0, -direction[0]], [-direction[1], direction[0], 0]])
        rotation = np.eye(3) * np.cos(a) + (1 - np.cos(a)) * np.outer(direction, direction) + np.sin(a) * cross
        observed = (expected @ basis.T) @ rotation.T + centre
        actual = MODS.turntable_coordinates.to_initial_table_frame(observed, 37, axis)
        np.testing.assert_allclose(actual, expected, atol=1e-12)

    def test_background_selection_preserves_xyz_and_reason(self):
        m = model()
        result = reconstruct.reconstruct_profile(np.array([[330., 240.], [390., 240.]]), 30, m, MODS)
        self.assertEqual(result['selected'].tolist(), [True, False])
        self.assertEqual(result['reasons'][1], 'outside_60mm_axis_radius')
        np.testing.assert_allclose(result['camera_xyz'][1], [70, 0, 100])
        m['reference_plane']['offset_mm'] = 102
        low = reconstruct.reconstruct_profile(np.array([[330., 240.]]), 30, m, MODS)
        self.assertEqual(low['reasons'][0], 'below_3mm_reference_height')
        self.assertFalse(low['selected'][0])
        np.testing.assert_allclose(low['table_xyz'], [[10, 0, 0]])

    def test_grazing_and_behind_camera_are_excluded(self):
        m = model()
        m['laser_plane'] = {'normal': [1, 0, 0], 'offset_mm': -10}
        result = reconstruct.reconstruct_profile(np.array([[320., 240.], [310., 240.]]), 30, m, MODS)
        self.assertFalse(result['valid'].any())
        self.assertFalse(result['selected'].any())
        self.assertEqual(result['reasons'], ['invalid_ray_plane_intersection'] * 2)

    def test_geometry_schema_gate_and_wide_axis(self):
        reconstruct.validate_geometry(model(), 10, MODS)
        with self.assertRaisesRegex(ValueError, 'large-angle'):
            reconstruct.validate_geometry(model(), 180, MODS)
        m = model()
        m['axis']['large_angle_axis_validation_passed'] = True
        reconstruct.validate_geometry(m, 180, MODS)
        m['shape_preview_candidate_usable'] = False
        with self.assertRaises(ValueError):
            reconstruct.validate_geometry(m, 5, MODS)

    def test_bad_K_nan_zero_and_nonorthogonal_basis_rejected(self):
        for edit in (
            lambda m: m['camera']['K'][0].__setitem__(0, -100),
            lambda m: m['axis'].__setitem__('motor_zero_reference_deg', float('nan')),
            lambda m: m['axis']['turntable_basis_camera_columns'][0].__setitem__(0, 2),
            lambda m: m.__setitem__('historical_extrinsics_used', True),
            lambda m: m.__setitem__('object_used_in_geometry_fit', True),
        ):
            m = model()
            edit(m)
            with self.assertRaises(ValueError):
                reconstruct.validate_geometry(m, 5, MODS)

    def test_PC_gate_precedes_input_reads_and_output(self):
        output = self.root / 'must_not_exist'
        with patch.object(MODS.board_pointcloud_export, 'board_identity',
                          return_value={'is_linux_arm_zynq_or_pynq': False}), \
                patch.object(reconstruct, 'inspect', side_effect=AssertionError('must not inspect')):
            with self.assertRaisesRegex(ValueError, 'actual Zynq'):
                reconstruct.reconstruct_board(PROJECT, self.root, self.root / 'missing.json', output, MODS)
        self.assertFalse(output.exists())

    def test_non_zynq_pynq_string_cannot_bypass_device_tree(self):
        identity = {'is_linux_arm_zynq_or_pynq': True, 'device_tree_model': 'PYNQ generic',
                    'device_tree_compatible': 'non-zyn-platform'}
        with patch.object(MODS.board_pointcloud_export, 'board_identity', return_value=identity):
            with self.assertRaisesRegex(ValueError, 'actual Zynq'):
                reconstruct.reconstruct_board(PROJECT, self.root, self.root / 'missing.json',
                                              self.root / 'output', MODS)

    def test_scan_simulated_or_failed_rejected_before_other_fields(self):
        for source in ({'status': 'failed'}, {'status': 'completed', 'simulation': True},
                       {'status': 'completed', 'real_hardware_acquisition_verified': True,
                        'hardware_accessed': True, 'simulation': True}):
            with self.assertRaisesRegex(ValueError, 'real hardware'):
                reconstruct.validate_scan(source, MODS)

    def test_full_60_degree_schema_counter_and_drift_rejection(self):
        # Exercise the real orchestration with explicit fake primitives. The
        # saved file stays simulation=true; only an isolated in-memory schema
        # fixture gets real-flag shapes. No inspect/reconstruct/export follows.
        from check_object_board_scan import FakeCamera, FakeDriver, FakeMotor
        from types import SimpleNamespace
        trace, driver = [], FakeDriver()
        motor, camera = FakeMotor(trace), FakeCamera(trace, driver)
        identity = {'bitfile': 'SYNTHETIC_SCHEMA_ONLY.bit', 'sha256': 'FAKE',
                    'timestamp': 'FAKE', 'fclk0_mhz': 50}
        runtime = SimpleNamespace(driver=driver, identity=identity,
                                  initial_diagnostics=driver.diagnostics(),
                                  read_identity=lambda: identity.copy())
        with patch.object(MODS.object_board_scan.time, 'sleep'), \
                patch.object(MODS.object_board_scan, 'progress'):
            saved = MODS.object_board_scan.run_acquisition(
                self.root, runtime, motor, camera, MODS.object_board_scan.nominal_plan(60),
                client_date='2026-10-20', roi=(175, 340, 400, 400), exposure=5, hardware=False)
        self.assertEqual(saved['status'], 'completed')
        self.assertTrue(reconstruct.load(self.root / 'completion.json')['simulation'])
        fixture = copy.deepcopy(saved)
        fixture.update(simulation=False, hardware_accessed=True, real_hardware_acquisition_verified=True)
        self.assertAlmostEqual(reconstruct.validate_scan(fixture, MODS), 60, delta=.3)
        self.assertEqual(fixture['completed_capture_batches'], 15)
        self.assertEqual(fixture['last_verified_completed_frames'], 68 + 45)
        fixture['last_verified_completed_frames'] += 3
        with self.assertRaisesRegex(ValueError, 'Final PL counter'):
            reconstruct.validate_scan(fixture, MODS)
        fixture['last_verified_completed_frames'] -= 3
        fixture['points'][2]['motor_position_after_capture_deg'] += .11
        with self.assertRaisesRegex(ValueError, 'drift'):
            reconstruct.validate_scan(fixture, MODS)

    def test_input_and_geometry_tamper_rejected(self):
        geometry = self.root / 'geometry.json'
        geometry.write_text('{}', encoding='utf-8')
        original = self.root / 'capture.json'
        original.write_text('{}', encoding='utf-8')
        inspection = {'geometry_sha256': reconstruct.digest(geometry),
                      'input_sha256': {'capture.json': reconstruct.digest(original)}}
        reconstruct.unchanged(self.root, geometry, inspection)
        original.write_text('{"changed":true}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Scan input changed'):
            reconstruct.unchanged(self.root, geometry, inspection)
        original.write_text('{}', encoding='utf-8')
        geometry.write_text('{"changed":true}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Geometry changed'):
            reconstruct.unchanged(self.root, geometry, inspection)

    def test_path_escape_rejected(self):
        for name in ('../outside', '/absolute', 'folder\\bad'):
            with self.assertRaises(ValueError):
                reconstruct.child(self.root, name)

    def historical_batch(self):
        """Copied real images used only for hash/PL tamper rejection, never XYZ."""
        evidence = PROJECT / 'results/integrated_uart_20261020_193135/evidence'
        folder = self.root / 'point_000/images'
        shutil.copytree(evidence / 'scan/point_00/images', folder)
        session = reconstruct.load(evidence / 'scan/session.json')
        batch = copy.deepcopy(session['points'][0]['capture'])
        # Its original provenance is untouched; canonical files are local copies.
        return folder, batch

    def test_nine_originals_schema_and_original_tamper(self):
        folder, batch = self.historical_batch()
        counter, hashes = reconstruct.verify_batch_files(self.root, 'point_000', batch,
                                                         (175, 340, 400, 400), 5, 59)
        self.assertEqual(counter, 62)
        self.assertEqual(sum(name.endswith('.png') for name in hashes), 9)
        with (folder / 'raw_00.png').open('ab') as stream:
            stream.write(b'ORIGINAL_TAMPER')
        with self.assertRaisesRegex(ValueError, 'Original image SHA256'):
            reconstruct.verify_batch_files(self.root, 'point_000', batch, (175, 340, 400, 400), 5, 59)

    def test_packed_PL_reserved_bits_and_counter_tamper(self):
        folder, batch = self.historical_batch()
        file = folder / 'pl_00_actual.bin'
        words = np.frombuffer(file.read_bytes(), '<u4').copy()
        words[0] |= np.uint32(0x40000000)
        file.write_bytes(words.tobytes())
        with self.assertRaisesRegex(ValueError, 'packing'):
            reconstruct.verify_batch_files(self.root, 'point_000', batch, (175, 340, 400, 400), 5, 59)
        words[0] &= np.uint32(0xbfffffff)
        file.write_bytes(words.tobytes())
        rows = reconstruct.load(folder / 'pl_transactions.json')
        rows[0]['completed_frames'] += 1
        reconstruct.save(folder / 'pl_transactions.json', rows)
        with self.assertRaisesRegex(ValueError, 'not continuous'):
            reconstruct.verify_batch_files(self.root, 'point_000', batch, (175, 340, 400, 400), 5, 59)

    def test_recomputed_PL_pixels_reject_saved_coordinate_tamper(self):
        folder, _ = self.historical_batch()
        # 2D quality reconstruction only; no pixel rays, real XYZ or export.
        pixels = reconstruct.actual_pl_pixels(folder, (175, 340, 400, 400), MODS)
        self.assertGreater(len(pixels), 0)
        pixels[0, 1] += 1 / 256
        np.savetxt(folder / 'stripe_pixels.csv', pixels, delimiter=',',
                   header='x_px,y_px', comments='')
        with self.assertRaisesRegex(ValueError, 'CSV differs'):
            reconstruct.actual_pl_pixels(folder, (175, 340, 400, 400), MODS)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MathAndEvidenceChecks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    reconstruct.save(BASE / 'latest_check_result.json', {
        'synthetic_math_and_tamper_checks_only': True, 'hardware_accessed': False,
        'real_XYZ_computed': False, 'real_model_exported': False,
        'tests_run': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'passed': result.wasSuccessful()})
    raise SystemExit(0 if result.wasSuccessful() else 1)
