"""Camera-frame ray/plane geometry in mm; no camera IO or empirical correction.

Plane convention: dot(normal, xyz_camera_mm) + offset_mm == 0.
The reference normal faces the camera, so height is positive toward the camera.
"""
import numpy as np


def unit_plane(normal, offset):
    n = np.asarray(normal, dtype=float)
    if n.shape != (3,) or not np.isfinite(n).all() or not np.isfinite(offset):
        raise ValueError("Plane requires a finite 3-vector and offset")
    scale = np.linalg.norm(n)
    if scale < 1e-12:
        raise ValueError("Plane normal is zero")
    return n / scale, float(offset) / scale


def intersect_rays(rays, normal, offset, min_sine=0.01):
    """NaN for parallel/grazing rays, intersections behind camera, bad inputs."""
    rays = np.asarray(rays, dtype=float)
    if rays.ndim != 2 or rays.shape[1] != 3:
        raise ValueError("Expected Nx3 camera rays")
    if not np.isfinite(min_sine) or not 0 < min_sine < 1:
        raise ValueError("min_sine must be between zero and one")
    n, d = unit_plane(normal, offset)
    lengths = np.linalg.norm(rays, axis=1)
    denom = rays @ n
    valid = (np.isfinite(rays).all(axis=1) & (lengths > 0)
             & (np.abs(denom) >= min_sine * lengths))
    t = np.full(len(rays), np.nan)
    np.divide(-d, denom, out=t, where=valid)
    valid &= (t > 0) & (rays[:, 2] > 0)
    xyz = rays * t[:, None]
    xyz[~valid] = np.nan
    return xyz, valid


def fit_plane(points, min_second_spread_mm=1.0):
    """TLS over ALL supplied points. Collinear/insufficient support is rejected."""
    p = np.asarray(points, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or len(p) < 6 or not np.isfinite(p).all():
        raise ValueError("Need at least six finite XYZ points")
    if not np.isfinite(min_second_spread_mm) or min_second_spread_mm <= 0:
        raise ValueError("Minimum spread must be positive")
    centre = p.mean(axis=0)
    _, s, vt = np.linalg.svd(p - centre, full_matrices=False)
    spread = s / np.sqrt(len(p))
    if spread[1] < min_second_spread_mm:
        raise ValueError("Laser observations are nearly one line; plane is underdetermined")
    n = vt[-1]
    d = -float(n @ centre)
    if d < 0:  # deterministic sign, not a height/reference convention
        n, d = -n, -d
    residual = p @ n + d
    return {"normal": n.tolist(), "offset_mm": d,
            "points": len(p), "spread_mm": spread.tolist(),
            "rms_mm": float(np.sqrt(np.mean(residual ** 2))),
            "p95_abs_mm": float(np.percentile(np.abs(residual), 95))}


def face_camera(normal, offset, surface_centre):
    n, d = unit_plane(normal, offset)
    if n @ (-np.asarray(surface_centre, dtype=float)) < 0:
        n, d = -n, -d
    return n, d


def reconstruct(rays, laser_plane, reference_plane, min_sine=0.01):
    xyz, valid = intersect_rays(rays, laser_plane['normal'],
                                laser_plane['offset_mm'], min_sine)
    n, d = unit_plane(reference_plane['normal'], reference_plane['offset_mm'])
    height = xyz @ n + d
    height[~valid] = np.nan
    return xyz, height, valid


def startup_action(camera_compatible, rig_evidence, reference_evidence,
                   reference_changed=False, rotation_requested=False,
                   axis_calibrated=False):
    """Consume INDEPENDENT evidence, never infer rig motion from one zero line.

    rig_evidence: 'verified', 'changed', or 'unknown'. 'verified' must come from
    a current multi-depth reference check or a known calibration target, not
    merely a user assertion, three ink marks, or the same empty laser line.
    reference_evidence: 'verified' or 'unknown'. All ready states remain
    conditional on upstream evidence, not metrological accuracy certification.
    """
    if rig_evidence not in ('verified', 'changed', 'unknown'):
        raise ValueError('Unknown rig evidence state')
    if reference_evidence not in ('verified', 'unknown'):
        raise ValueError('Unknown reference evidence state')
    if not camera_compatible:
        return 'check_camera_mode_or_intrinsics'
    if rig_evidence == 'changed':
        return 'calibrate_laser_geometry'
    if rig_evidence == 'unknown':
        return 'check_rig_geometry'  # no forced full calibration, no auto zero fix
    if reference_evidence != 'verified':
        return 'measure_reference_plane'
    if reference_changed:
        return 'update_reference_only'
    if rotation_requested and not axis_calibrated:
        return 'calibrate_rotation_axis'
    return 'ready_for_sample_validation'
