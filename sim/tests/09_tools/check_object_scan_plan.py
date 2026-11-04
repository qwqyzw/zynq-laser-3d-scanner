"""Offline audit of a finite object-scan route; never accesses hardware.

The planned angles are relative to ONE saved motor-feedback origin. This
checks command geometry, not physical cable clearance or calibrated angles.
It does not change the frozen small-scan limits or authorize fault recovery.

JSON schema for --plan:
  global_origin_motor_deg: finite number
  acquisition_angles_deg: [0, 5, ..., at most 180]
  return_angles_deg: [last_acquisition - 5, ..., 0]
Optional stage_records contain global_origin_motor_deg,
stage_origin_angle_deg, local_angles_deg, global_angles_deg, and
absolute_targets_deg. Adjacent stages may share their boundary anchor.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import sys
import unittest


def finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be a finite number')
    return float(value)


def validate_object_route(global_origin_motor_deg, acquisition_angles_deg,
                          return_angles_deg, *, stage_records=None):
    origin = finite_number(global_origin_motor_deg, 'global origin')
    acquire = [finite_number(value, 'acquisition angle') for value in acquisition_angles_deg]
    returning = [finite_number(value, 'return angle') for value in return_angles_deg]
    if not 2 <= len(acquire) <= 37 or acquire[0] != 0 or not 0 < acquire[-1] <= 180:
        raise ValueError('Acquire 2..37 poses from zero to a nominal endpoint at most 180 degrees')
    if any(not 0 <= angle <= 180 for angle in acquire + returning):
        raise ValueError('Every nominal waypoint must remain within global 0..180 degrees')
    if any(abs((b - a) - 5) > 1e-9 for a, b in zip(acquire, acquire[1:])):
        raise ValueError('Acquisition must advance monotonically by five degrees')
    if len(returning) != len(acquire) - 1 or not returning or returning[-1] != 0:
        raise ValueError('Return must contain one reverse waypoint per forward step and finish at zero')
    backwards = [acquire[-1]] + returning
    if any(abs((a - b) - 5) > 1e-9 for a, b in zip(backwards, backwards[1:])):
        raise ValueError('Return must reverse monotonically by five degrees, without a direct long jump')
    if stage_records is not None:
        flattened = []
        previous_stage_end = None
        for stage in stage_records:
            if finite_number(stage['global_origin_motor_deg'], 'stage global origin') != origin:
                raise ValueError('A stage must not reset or replace the saved global motor origin')
            anchor = finite_number(stage['stage_origin_angle_deg'], 'stage relative anchor')
            local = [finite_number(value, 'local angle') for value in stage['local_angles_deg']]
            global_angles = [finite_number(value, 'stage global angle') for value in stage['global_angles_deg']]
            targets = [finite_number(value, 'absolute motor target') for value in stage['absolute_targets_deg']]
            if not 2 <= len(local) <= 13 or len(local) != len(global_angles) or len(local) != len(targets):
                raise ValueError('Each stage needs 2..13 matched local/global/absolute records')
            if local[0] != 0 or any(not 0 <= value <= 60 for value in local):
                raise ValueError('Stage local coordinates must start at zero and stay within 60 degrees')
            if any(abs((b - a) - 5) > 1e-9 for a, b in zip(local, local[1:])):
                raise ValueError('Stage local increments must be five degrees')
            if previous_stage_end is not None and global_angles[0] != previous_stage_end:
                raise ValueError('The next stage must share the prior global boundary anchor')
            for local_angle, global_angle, target in zip(local, global_angles, targets):
                if abs(global_angle - anchor - local_angle) > 1e-9:
                    raise ValueError('Stage local/global angle mapping disagrees')
                if abs(target - origin - global_angle) > 1e-9:
                    raise ValueError('Absolute motor target must use the original global origin')
                if not flattened or global_angle != flattened[-1]:
                    flattened.append(global_angle)
            previous_stage_end = global_angles[-1]
        if flattened != acquire:
            raise ValueError('Stage metadata must cover exactly the global acquisition route')
    travel = sum(abs(b - a) for a, b in zip(acquire + returning, (acquire + returning)[1:]))
    return {
        'scope': 'offline_nominal_command_route_only_no_hardware',
        'global_origin_motor_deg': origin,
        'acquisition_pose_count': len(acquire),
        'forward_finite_moves': len(acquire) - 1,
        'return_finite_moves': len(returning),
        'nominal_maximum_relative_angle_deg': acquire[-1],
        'nominal_total_travel_deg': travel,
        'nominal_return_to_global_origin': True,
        'stage_origins_are_metadata_only': True,
        'physical_cable_clearance_verified': False,
        'physical_angle_accuracy_verified': False,
        'requires_runtime_feedback_envelope_and_fault_checks': True,
    }


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.origin = -0.0494384765625
        self.acquire = list(range(0, 181, 5))
        self.returning = list(range(175, -1, -5))
        self.stages = []
        for anchor in (0, 60, 120):
            local = list(range(0, 61, 5))
            global_angles = [anchor + value for value in local]
            self.stages.append({
                'global_origin_motor_deg': self.origin,
                'stage_origin_angle_deg': anchor,
                'local_angles_deg': local,
                'global_angles_deg': global_angles,
                'absolute_targets_deg': [self.origin + value for value in global_angles],
            })

    def test_complete_half_turn_and_reverse_route(self):
        result = validate_object_route(self.origin, self.acquire, self.returning,
                                       stage_records=self.stages)
        self.assertEqual(result['acquisition_pose_count'], 37)
        self.assertEqual(result['nominal_total_travel_deg'], 360)
        self.assertEqual(result['nominal_maximum_relative_angle_deg'], 180)
        self.assertFalse(result['physical_cable_clearance_verified'])

    def test_all_shorter_five_degree_prefixes_return(self):
        for endpoint in range(5, 181, 5):
            with self.subTest(endpoint=endpoint):
                validate_object_route(self.origin, list(range(0, endpoint + 1, 5)),
                                      list(range(endpoint - 5, -1, -5)))

    def test_outward_accumulated_wrap_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_object_route(self.origin, list(range(0, 361, 5)), list(range(355, -1, -5)))

    def test_direct_long_return_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_object_route(self.origin, self.acquire, [0])

    def test_missing_return_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_object_route(self.origin, self.acquire, self.returning[:-1])

    def test_return_continuing_forward_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_object_route(self.origin, [0, 5, 10], [15, 0])

    def test_acquisition_negative_or_missing_step_is_rejected(self):
        for angles in ([0, -5], [0, 10], [0, 5, 5], [5, 10]):
            with self.subTest(angles=angles), self.assertRaises(ValueError):
                validate_object_route(self.origin, angles, [0])

    def test_nonfinite_and_bool_are_rejected(self):
        for value in (float('nan'), float('inf'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_object_route(value, [0, 5], [0])
            with self.subTest(angle=value), self.assertRaises(ValueError):
                validate_object_route(self.origin, [0, value], [0])

    def test_stage_origin_reset_is_rejected(self):
        self.stages[1]['global_origin_motor_deg'] += 60
        with self.assertRaisesRegex(ValueError, 'must not reset'):
            validate_object_route(self.origin, self.acquire, self.returning, stage_records=self.stages)

    def test_using_local_instead_of_global_motor_target_is_rejected(self):
        self.stages[1]['absolute_targets_deg'] = [self.origin + value
                                                 for value in self.stages[1]['local_angles_deg']]
        with self.assertRaisesRegex(ValueError, 'original global origin'):
            validate_object_route(self.origin, self.acquire, self.returning, stage_records=self.stages)

    def test_segment_gap_or_duplicate_segment_is_rejected(self):
        for stages in (self.stages[1:], [self.stages[0], copy.deepcopy(self.stages[0]), self.stages[2]]):
            with self.subTest(stages=stages), self.assertRaises(ValueError):
                validate_object_route(self.origin, self.acquire, self.returning, stage_records=stages)

    def test_existing_frozen_apis_still_enforce_their_limits(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '04_runtime'))
        from rotation_scan import validate_angles
        from motor_position import MotorGeometry, plan_relative_move, position_frame
        for stage in self.stages:
            validate_angles(stage['local_angles_deg'], max_angle_deg=60, max_points=13)
        with self.assertRaises(ValueError):
            validate_angles(self.acquire, max_angle_deg=180, max_points=37)
        geometry = MotorGeometry(1.8, 16)
        for delta in (5, -5):
            plan = plan_relative_move(delta, geometry, rpm=1)
            self.assertEqual(plan['pulses'], 44)
            self.assertEqual(position_frame(1, plan)[1], 0xFD)
        with self.assertRaises(ValueError):
            plan_relative_move(-180, geometry, rpm=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(RouteTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.plan is None:
        parser.error('Choose --self-test or --plan; this tool never operates hardware')
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    result = validate_object_route(plan['global_origin_motor_deg'], plan['acquisition_angles_deg'],
                                   plan['return_angles_deg'], stage_records=plan.get('stage_records'))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
