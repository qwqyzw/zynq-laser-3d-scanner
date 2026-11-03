"""Undo a measured turntable rotation; no geometry fitting or height correction.

Coordinates inherit the supplied axis and camera calibration uncertainty.
A single profile is not a complete 3D scan.
"""
import cv2
import numpy as np


def to_initial_table_frame(points_camera_mm, motor_relative_angle_deg, axis):
    if not axis.get('candidate_checks_passed', False):
        raise ValueError('Rotation axis has not passed its candidate checks')
    points = np.asarray(points_camera_mm, dtype=float)
    centre = np.asarray(axis['axis_point_on_initial_paper_mm'], dtype=float)
    direction = np.asarray(axis['axis_direction_camera'], dtype=float)
    basis = np.asarray(axis['turntable_basis_camera_columns'], dtype=float)
    if (points.ndim != 2 or points.shape[1] != 3 or centre.shape != (3,)
            or direction.shape != (3,) or basis.shape != (3, 3)
            or not all(np.isfinite(a).all() for a in (points, centre, direction, basis))
            or not np.isfinite(motor_relative_angle_deg)):
        raise ValueError('Need finite points, angle, centre and orthonormal basis')
    if (not np.isclose(np.linalg.norm(direction), 1, atol=1e-6)
            or not np.allclose(basis.T @ basis, np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(basis), 1, atol=1e-6)):
        raise ValueError('Invalid rotation axis or table basis')
    rotation = cv2.Rodrigues(direction * np.radians(motor_relative_angle_deg))[0]
    # Row vectors: undo forward Q.T, then project onto the initial basis.
    return ((points - centre) @ rotation) @ basis
