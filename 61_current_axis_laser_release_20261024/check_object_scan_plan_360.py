"""Offline audit of a finite object-scan route; never accesses hardware.

The planned angles are relative to ONE saved motor-feedback origin. This
checks command geometry, not physical cable clearance or calibrated angles.
It does not change the frozen small-scan limits or authorize fault recovery.

JSON schema for --plan:
  global_origin_motor_deg: finite number
  acquisition_angles_deg: [0, 5, ..., at most 360]
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
    if len(acquire) != 73 or acquire != list(range(0, 361, 5)):
        raise ValueError('Acquire 2..73 poses from zero to a nominal endpoint at most 360 degrees')
    if any(not 0 <= angle <= 360 for angle in acquire + returning):
        raise ValueError('Every nominal waypoint must remain within global 0..360 degrees')
    if any(abs((b - a) - 5) > 1e-9 for a, b in zip(acquire, acquire[1:])):
        raise ValueError('Acquisition must advance monotonically by five degrees')
    if returning:
        raise ValueError('One complete forward turn closes physical orientation; no reverse route permitted')
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
        'nominal_return_to_global_origin': False,
        'nominal_full_turn_orientation_closure': True,
        'stage_origins_are_metadata_only': True,
        'physical_cable_clearance_verified': False,
        'physical_angle_accuracy_verified': False,
        'requires_runtime_feedback_envelope_and_fault_checks': True,
    }
