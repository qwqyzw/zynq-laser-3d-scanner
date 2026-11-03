"""Offline pinhole camera + laser plane + reference plane workflow.

No live camera access, no use of old per-column empirical height parameters.
All thresholds are engineering checks, never claims of instrument accuracy.
Requires OpenCV; outputs are candidates pending real independent validation.
"""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from geometry_core import fit_plane, face_camera, intersect_rays, reconstruct
from laser_horizontal_check import extract_horizontal
from laser_vertical_fast import _score_crop


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_new(path, record):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2, allow_nan=False)


def object_points(board):
    cols, rows = board['inner_cols'], board['inner_rows']
    if (int(cols) != cols or int(rows) != rows or cols < 4 or rows < 4
            or not board.get('scale_measured', False)):
        raise ValueError('Need measured target scale and >=4x4 internal corners')
    cols, rows = int(cols), int(rows)
    sx, sy = board['cell_x_mm'], board['cell_y_mm']
    if not np.isfinite([sx, sy]).all() or min(sx, sy) <= 0:
        raise ValueError('Measured cell spacings must be positive')
    p = np.zeros((cols * rows, 3), np.float32)
    p[:, :2] = np.mgrid[:cols, :rows].T.reshape(-1, 2) * [sx, sy]
    return p


def read_image(path, size=None):
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('Cannot read image: ' + str(path))
    if size is not None and list(image.shape[1::-1]) != list(size):
        raise ValueError('Image size differs from camera calibration: ' + str(path))
    return image


def corners(image, board):
    pattern = (int(board['inner_cols']), int(board['inner_rows']))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    ok, found = cv2.findChessboardCorners(
        gray, pattern, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok:
        raise ValueError('Complete chessboard not detected; no guessed corners')
    return cv2.cornerSubPix(gray, found, (5, 5), (-1, -1),
        (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 50, 0.001))


def camera_arrays(camera):
    k = np.asarray(camera['K'], dtype=float)
    d = np.asarray(camera['dist_coeffs'], dtype=float)
    if (k.shape != (3, 3) or not np.isfinite(k).all() or not np.isfinite(d).all()
            or k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1])
            or d.size not in (4, 5, 8, 12, 14)):
        raise ValueError('Invalid camera model')
    return k, d


def pixel_rays(pixels, camera):
    p = np.asarray(pixels, dtype=float).reshape(-1, 2)
    size = camera['image_size']
    if (not np.isfinite(p).all() or (p < 0).any()
            or (p >= np.asarray(size)).any()):
        raise ValueError('Pixels outside calibrated original image')
    k, d = camera_arrays(camera)
    uv = cv2.undistortPoints(p.reshape(-1, 1, 2), k, d).reshape(-1, 2)
    return np.column_stack([uv, np.ones(len(uv))])


def board_pose(image, board, camera, max_reprojection_px=1.0):
    p = object_points(board)
    found = corners(image, board)
    k, d = camera_arrays(camera)
    ok, rvec, tvec = cv2.solvePnP(p, found, k, d, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise ValueError('Board pose failed')
    rot = cv2.Rodrigues(rvec)[0]
    xyz = p @ rot.T + tvec.ravel()
    projected = cv2.projectPoints(p, rvec, tvec, k, d)[0].reshape(-1, 2)
    rms = float(np.sqrt(np.mean(np.sum((projected - found.reshape(-1, 2)) ** 2, axis=1))))
    if (xyz[:, 2] <= 0).any() or rms > max_reprojection_px:
        raise ValueError('Board pose behind camera or reprojection residual too high')
    n, offset = face_camera(rot[:, 2], -rot[:, 2] @ tvec.ravel(), xyz.mean(axis=0))
    return {'normal': n.tolist(), 'offset_mm': offset,
            'reprojection_rms_px': rms, 'xyz_corners_mm': xyz.tolist(),
            'image_corners': found.reshape(-1, 2).tolist(),
            'R': rot.tolist(), 't_mm': tvec.ravel().tolist()}


def camera_calibration(manifest_path, out):
    m = load(manifest_path); root = Path(manifest_path).parent
    board = m['board']; obj = object_points(board)
    if len(m['images']) < 8:
        raise ValueError('Need >=8 diverse board views, not duplicates of one pose')
    images = [root / f for f in m['images']]
    if len({digest(f) for f in images}) != len(images):
        raise ValueError('Duplicate image cannot add a calibration view')
    size = read_image(images[0]).shape[1::-1]
    observed = [corners(read_image(f, size), board) for f in images]
    rms, k, d, rv, tv = cv2.calibrateCamera([obj] * len(images), observed, size, None, None)
    per_view = []
    normals = []
    for f, c, r, t in zip(images, observed, rv, tv):
        predicted = cv2.projectPoints(obj, r, t, k, d)[0].reshape(-1, 2)
        e = float(np.sqrt(np.mean(np.sum((predicted - c.reshape(-1, 2)) ** 2, axis=1))))
        per_view.append({'image': str(f), 'sha256': digest(f), 'rms_px': e})
        normals.append(cv2.Rodrigues(r)[0][:, 2])
    normals = np.asarray(normals)
    angles = np.degrees(np.arccos(np.clip(np.abs(normals @ normals.T), 0, 1)))
    gates = {'global_reprojection_rms_le_1px': rms <= 1,
             'all_view_rms_le_1_5px': all(v['rms_px'] <= 1.5 for v in per_view),
             'normal_diversity_ge_15deg': float(angles.max()) >= 15,
             'focal_positive': bool(k[0, 0] > 0 and k[1, 1] > 0),
             'principal_point_in_image': bool(0 <= k[0, 2] < size[0] and 0 <= k[1, 2] < size[1])}
    # Reject fold-over for the standard five-coefficient radial model on this FOV.
    rr = np.linspace(0, max(np.hypot((u-k[0, 2])/k[0, 0], (v-k[1, 2])/k[1, 1])
                    for u in (0, size[0]-1) for v in (0, size[1]-1)), 300)
    k1, k2, _, _, k3 = d.ravel()
    gates['radial_derivative_positive_on_fov'] = bool((1+3*k1*rr**2+5*k2*rr**4+7*k3*rr**6 > 0).all())
    record = {'kind': 'camera_candidate', 'K': k.tolist(), 'dist_coeffs': d.ravel().tolist(),
              'image_size': list(size), 'board': board, 'views': per_view,
              'observed_corner_hull_px': cv2.convexHull(np.concatenate(observed).reshape(-1, 2)).reshape(-1, 2).tolist(),
              'rms_px': float(rms), 'normal_diversity_deg': float(angles.max()),
              'gates': gates, 'candidate_checks_passed': all(gates.values()),
              'accuracy_verified': False, 'opencv_version': cv2.__version__,
              'manifest_sha256': digest(manifest_path)}
    save_new(out, record)
    return record


def paired_stripe(folder, roi, camera, expected_exposure=20):
    """Known target pose before/after; do not align images or silently drop frames."""
    folder = Path(folder)
    cap = load(folder / 'capture.json')
    mode = load(folder / 'mode.json')
    if expected_exposure not in (10, 20) or cap.get('exposure_trial', 20) != expected_exposure:
        raise ValueError('Capture must document the explicitly selected exposure10 or20')
    if not (cap['status'] == 'completed' and cap['camera_release_completed']
            and cap['ae_exposure_restoration_verified'] and not cap.get('restore_errors')
            and len(cap['frames']) == 3 and len(cap['bright_frames']) == 6):
        raise ValueError('Capture/release/restoration evidence incomplete')
    if (mode['fourcc'] != 'MJPG' or [mode['width'], mode['height']] != camera['image_size']):
        raise ValueError('Capture mode differs from calibrated MJPG image mode')
    for f in cap['frames'] + cap['bright_frames']:
        if digest(folder / f['file']) != f['sha256']:
            raise ValueError('Photo hash mismatch')
    for f in cap['frames']:
        c = f['controls_readback']
        if c['ae'] != 1 or c['exp'] != expected_exposure:
            raise ValueError('Fixed exposure readback differs from declared capture exposure')
    a = board_pose(read_image(folder / 'bright_before_01.png', camera['image_size']), camera['board'], camera)
    b = board_pose(read_image(folder / 'bright_after_01.png', camera['image_size']), camera['board'], camera)
    pa, pb = np.asarray(a['xyz_corners_mm']), np.asarray(b['xyz_corners_mm'])
    movement = float(np.linalg.norm(pa - pb, axis=1).max())
    if movement > 1.0:
        raise ValueError('Target moved or corner ordering/pose uncertain within capture (>1mm)')
    # Avoid laser points on the table/background: only use the inner corner hull.
    hull = cv2.convexHull(np.asarray(a['image_corners'], np.float32))
    ys = []
    for frame in cap['frames']:
        image = read_image(folder / frame['file'], camera['image_size'])
        stripe = extract_horizontal(image, roi, threshold=12, min_run=8)
        scores = _score_crop(image.transpose(1, 0, 2), (roi[1], roi[0], roi[3], roi[2]))
        keep = stripe['valid'].copy()
        for x in np.flatnonzero(keep):
            y = float(stripe['y'][x]); iy = round(y)
            band = image[max(0, iy-10):min(image.shape[0], iy+11), x]
            profile = scores[x-roi[0]]; peak = int(np.argmax(profile))
            maxima = np.flatnonzero((profile[1:-1] >= profile[:-2]) & (profile[1:-1] > profile[2:]))+1
            other = maxima[(abs(maxima-peak) >= 4) & (abs(maxima-peak) <= 10)]
            keep[x] = (cv2.pointPolygonTest(hull, (float(x), y), True) >= 5
                       and y-roi[1] >= 5 and roi[3]-1-y >= 5
                       and not (band[:, 2] >= 250).any()
                       and not (band.min(axis=1) >= 245).any()
                       and not (profile[other] >= .7*profile[peak]).any())
        ys.append(np.where(keep, stripe['y'], np.nan))
    ys = np.asarray(ys); common = np.isfinite(ys).all(axis=0)
    valid = common.copy()
    valid[common] &= np.ptp(ys[:, common], axis=0) <= 1.5
    x = np.flatnonzero(valid)
    if len(x) < 20:
        raise ValueError('Fewer than20 stable quality stripe columns on calibration target')
    pixels = np.column_stack([x, np.median(ys[:, x], axis=0)])
    rays = pixel_rays(pixels, camera)
    xyz, good = intersect_rays(rays, a['normal'], a['offset_mm'])
    if not good.all():
        raise ValueError('Board ray intersection invalid or grazing')
    return xyz, pixels, {'folder': str(folder), 'capture_sha256': digest(folder / 'capture.json'),
                        'board_pose': a, 'board_pose_after': b, 'max_pose_change_mm': movement,
                        'quality_columns': len(x), 'fixed_exposure': expected_exposure}


def laser_calibration(manifest_path, camera_path, out):
    camera = load(camera_path)
    if not camera.get('candidate_checks_passed'):
        raise ValueError('Camera candidate checks have not passed')
    m = load(manifest_path); root = Path(manifest_path).parent
    groups = m['groups']
    if (sum(g['role'] == 'fit' for g in groups) < 3
            or sum(g['role'] == 'check' for g in groups) < 1
            or any(g['role'] not in ('fit', 'check') for g in groups)):
        raise ValueError('Need >=3 fit target poses and >=1 unused check pose')
    names = [str((root / g['folder']).resolve()) for g in groups]
    if len(set(names)) != len(names):
        raise ValueError('A target group cannot be both fit and check')
    observations = [paired_stripe(root/g['folder'], tuple(g['roi']), camera,
                                 expected_exposure=g.get('exposure', 20)) for g in groups]
    hashes = [o[2]['capture_sha256'] for o in observations]
    if len(set(hashes)) != len(hashes):
        raise ValueError('Copied capture cannot be an independent target group')
    train = np.concatenate([o[0] for g, o in zip(groups, observations) if g['role'] == 'fit'])
    plane = fit_plane(train)
    checks = []
    for g, (xyz, pixels, evidence) in zip(groups, observations):
        residual = np.abs(xyz @ plane['normal'] + plane['offset_mm'])
        checks.append({'role': g['role'], 'evidence': evidence,
                       'p95_plane_residual_mm': float(np.percentile(residual, 95)),
                       'pixels': pixels.tolist()})
    gates = {'fit_rms_le_0_5mm': plane['rms_mm'] <= .5,
             'all_group_p95_le_1mm': all(c['p95_plane_residual_mm'] <= 1 for c in checks)}
    record = {'kind': 'laser_plane_candidate', **plane, 'camera_sha256': digest(camera_path),
              'groups': checks, 'gates': gates, 'candidate_checks_passed': all(gates.values()),
              'accuracy_verified': False, 'check_poses_used_for_fit': False,
              'manifest_sha256': digest(manifest_path),
              'scope': 'Camera-frame metric ray/laser intersection; target-plane residual is NOT height accuracy'}
    save_new(out, record)
    return record


def reference_plane(image_path, camera_path, out, surface_offset_mm=0):
    camera = load(camera_path)
    if not camera.get('candidate_checks_passed'):
        raise ValueError('Camera candidate checks have not passed')
    if not np.isfinite(surface_offset_mm) or surface_offset_mm < 0:
        raise ValueError('Target surface thickness above platform must be known and nonnegative')
    pose = board_pose(read_image(image_path, camera['image_size']), camera['board'], camera)
    # board plane is above platform by thickness along the normal facing camera.
    record = {**pose, 'offset_mm': pose['offset_mm'] + surface_offset_mm,
              'kind': 'reference_plane_candidate', 'surface_offset_mm': surface_offset_mm,
              'camera_sha256': digest(camera_path), 'image_sha256': digest(image_path),
              'accuracy_verified': False}
    save_new(out, record)
    return record


def reconstruct_csv(camera_path, laser_path, reference_path, input_csv, out):
    camera, laser, reference = load(camera_path), load(laser_path), load(reference_path)
    if not (camera.get('candidate_checks_passed') and laser.get('candidate_checks_passed')):
        raise ValueError('Geometric calibration candidates not checked')
    if laser['camera_sha256'] != digest(camera_path) or reference['camera_sha256'] != digest(camera_path):
        raise ValueError('Mixed camera calibration versions')
    if Path(out).exists():
        raise FileExistsError(out)
    rows = np.atleast_1d(np.genfromtxt(input_csv, delimiter=',', names=True))
    if not {'x_px', 'y_px', 'valid'} <= set(rows.dtype.names or ()):
        raise ValueError('Expected original-image x_px,y_px,valid CSV')
    flags = rows['valid']
    if not np.isin(flags, [0, 1]).all():
        raise ValueError('Input valid flags must be0 or1')
    use = flags == 1
    xyz = np.full((len(rows), 3), np.nan); h = np.full(len(rows), np.nan)
    valid = np.zeros(len(rows), bool)
    if use.any():
        pixels = np.column_stack([rows['x_px'][use], rows['y_px'][use]])
        hull = np.asarray(camera['observed_corner_hull_px'], np.float32)
        if any(cv2.pointPolygonTest(hull, tuple(map(float, p)), False) < 0 for p in pixels):
            raise ValueError('Points outside camera target coverage; no unvalidated FOV extrapolation')
        rays = pixel_rays(pixels, camera)
        xyz[use], h[use], valid[use] = reconstruct(rays, laser, reference)
    with Path(out).open('x') as f:
        np.savetxt(f, np.column_stack([rows['x_px'], rows['y_px'], xyz, h, valid]),
                   delimiter=',', header='x_px,y_px,X_camera_mm,Y_camera_mm,Z_camera_mm,height_mm,valid', comments='')
    return {'points': len(rows), 'valid': int(valid.sum()), 'accuracy_verified': False,
            'warning': 'Ray geometry only; upstream stripe quality, current rig validity and FOV validation still required'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('camera'); c.add_argument('manifest'); c.add_argument('out')
    l = sub.add_parser('laser'); l.add_argument('manifest'); l.add_argument('camera'); l.add_argument('out')
    f = sub.add_parser('reference'); f.add_argument('image'); f.add_argument('camera'); f.add_argument('out')
    f.add_argument('--target-thickness-mm', type=float, default=0)
    m = sub.add_parser('measure')
    for key in ('camera', 'laser', 'reference', 'points', 'out'): m.add_argument(key)
    a = p.parse_args()
    if a.action == 'camera': r = camera_calibration(a.manifest, a.out)
    elif a.action == 'laser': r = laser_calibration(a.manifest, a.camera, a.out)
    elif a.action == 'reference': r = reference_plane(a.image, a.camera, a.out, a.target_thickness_mm)
    else: r = reconstruct_csv(a.camera, a.laser, a.reference, a.points, a.out)
    print(json.dumps({k: v for k, v in r.items() if k not in ('groups', 'views', 'xyz_corners_mm', 'image_corners')}, indent=2))


if __name__ == '__main__':
    main()
