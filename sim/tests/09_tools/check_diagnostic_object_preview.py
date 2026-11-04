"""Five isolated failed-axis preview tests; no board or real XYZ/export."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import reconstruct_object_on_zynq as r
from check_reconstruct_object_on_zynq import model, MODS, PROJECT


def diagnostic_model():
    value = model()
    value.update(diagnostic_preview_only=True, shape_preview_candidate_usable=False,
                 current_laser_geometry_verified_for_preview=True,
                 diagnostic_axis_max_xyz_rms_mm=1.36,
                 current_view_validation={
                     'geometry_method': 'current_base_rigid_transport_with_independent_target_checks',
                     'gates': {'current_base_holdout_passed': True, 'independent_laser_holdout_passed': True},
                     'input_sha256': {'SYNTHETIC_MATH_ONLY': '0' * 64},
                     'laser_checks': {'independent_one': {'count': 30, 'rms_mm': .3},
                                      'independent_two': {'count': 40, 'rms_mm': .4}}})
    value['axis'].update(candidate_checks_passed=False, large_angle_axis_validation_passed=False,
                         gates={'heldout_corner_rms_le_0_75mm': False, 'orientation_le_1deg': True},
                         residuals=[{'corner_xyz_rms_mm': 1.2}, {'corner_xyz_rms_mm': 1.36}])
    return value


class DiagnosticChecks(unittest.TestCase):
    def test_01_explicit_marker_required_and_strict_default_still_rejects(self):
        value = diagnostic_model()
        with self.assertRaisesRegex(ValueError, 'not usable'):
            r.validate_geometry(value, 60, MODS)
        value['diagnostic_preview_only'] = False
        with self.assertRaisesRegex(ValueError, 'explicitly marked'):
            r.validate_geometry(value, 60, MODS, diagnostic_preview=True)

    def test_02_current_laser_and_independent_base_checks_cannot_be_bypassed(self):
        mutations = (
            lambda v: v.__setitem__('current_laser_geometry_verified_for_preview', False),
            lambda v: v['current_view_validation']['gates'].__setitem__('current_base_holdout_passed', False),
            lambda v: v['current_view_validation'].__setitem__('gates', {}),
            lambda v: v['current_view_validation']['laser_checks']['independent_two'].__setitem__('rms_mm', 1.5),
            lambda v: v['current_view_validation'].__setitem__('input_sha256', {}),
        )
        for mutation in mutations:
            value = diagnostic_model()
            mutation(value)
            with self.assertRaises(ValueError):
                r.validate_geometry(value, 60, MODS, diagnostic_preview=True)

    def test_03_PC_still_refuses_before_input_read_or_XYZ(self):
        base = PROJECT / 'results/object_reconstruction_offline_checks'
        base.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix='diagnostic_PC_refusal_', dir=base))
        with patch.object(MODS.board_pointcloud_export, 'board_identity',
                          return_value={'is_linux_arm_zynq_or_pynq': False}), \
                patch.object(r, 'inspect', side_effect=AssertionError('must not read model')):
            with self.assertRaisesRegex(ValueError, 'actual Zynq'):
                r.reconstruct_board(PROJECT, root, root / 'geometry.json', root / 'output', MODS,
                                    diagnostic_preview=True)
        self.assertFalse((root / 'output').exists())

    def test_04_math_keeps_failed_gates_and_axes_unchanged(self):
        value = diagnostic_model()
        original = copy.deepcopy(value)
        r.validate_geometry(value, 60, MODS, diagnostic_preview=True)
        result = r.reconstruct_profile(np.array([[330., 240.]]), 120, value, MODS,
                                       diagnostic_preview=True)
        np.testing.assert_allclose(result['table_xyz'], [[0, -10, 0]], atol=1e-12)
        self.assertEqual(value, original)
        self.assertFalse(value['axis']['candidate_checks_passed'])
        value['axis']['turntable_basis_camera_columns'][0][0] = 2
        with self.assertRaisesRegex(ValueError, 'orthonormal'):
            r.validate_geometry(value, 60, MODS, diagnostic_preview=True)

    def test_05_visible_unaccepted_label_and_max_residual_are_bound(self):
        value = diagnostic_model()
        metadata = r.diagnostic_preview_metadata(value)
        self.assertEqual(metadata['title'], '实验点云预览（拼接检查未通过）')
        self.assertFalse(metadata['alignment_validated'])
        self.assertFalse(metadata['metric_accuracy_accepted'])
        self.assertIn('1.360000', metadata['geometry_status'])
        self.assertEqual(metadata['axis_max_xyz_rms_mm'], 1.36)
        value['diagnostic_axis_max_xyz_rms_mm'] = .7
        with self.assertRaisesRegex(ValueError, 'Maximum axis error differs'):
            r.diagnostic_preview_metadata(value)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DiagnosticChecks))
    base = PROJECT / 'results/object_reconstruction_offline_checks'
    base.mkdir(parents=True, exist_ok=True)
    r.save(base / 'diagnostic_check_result.json', {
        'synthetic_math_only': True, 'hardware_accessed': False,
        'real_XYZ_computed': False, 'real_export_performed': False,
        'tests_run': result.testsRun, 'passed': result.wasSuccessful(),
        'failures': len(result.failures), 'errors': len(result.errors)})
    raise SystemExit(0 if result.wasSuccessful() else 1)
