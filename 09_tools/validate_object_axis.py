"""Validate/refit a current multi-angle target axis on saved Zynq scan images.

No camera/motor access. Only labelled visible checkerboard corners enter pose
and axis fitting. No object XYZ is calculated. Historical laser/intrinsic
parameters are neither refitted nor silently replaced.
"""
import argparse
import copy
import json
import platform
import traceback
from pathlib import Path

import cv2
import numpy as np

from calibrate_current_targets import load, digest, read_image, refine, pose, rms


def project(obj, rot, trans, camera):
    return cv2.projectPoints(np.asarray(obj, float), cv2.Rodrigues(rot)[0],
                             np.asarray(trans, float), np.asarray(camera["K"], float),
                             np.asarray(camera["dist_coeffs"], float))[0].reshape(-1, 2)


def quadrant_descriptor(gray, obj, measured, rot, trans, camera, cell):
    """Four board-aligned quadrants must retain the reference black/white parity."""
    centre = project(obj, rot, trans, camera)
    values = []
    for sx, sy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
        shifted = obj + [sx * cell[0] * .22, sy * cell[1] * .22, 0]
        sample = measured + project(shifted, rot, trans, camera) - centre
        rows = []
        for x, y in sample:
            ix, iy = int(round(x)), int(round(y))
            if not 1 <= ix < gray.shape[1] - 1 or not 1 <= iy < gray.shape[0] - 1:
                rows.append(np.nan)
            else:
                rows.append(float(gray[iy-1:iy+2, ix-1:ix+2].mean()))
        values.append(rows)
    v = np.asarray(values).T
    polarity = (v[:, 0] + v[:, 2]) - (v[:, 1] + v[:, 3])
    contrast = np.where(polarity >= 0,
                        np.minimum(v[:, 0], v[:, 2]) - np.maximum(v[:, 1], v[:, 3]),
                        np.minimum(v[:, 1], v[:, 3]) - np.maximum(v[:, 0], v[:, 2]))
    return polarity, contrast


def lk(previous, current, points, initial=None):
    source = np.asarray(points, np.float32).reshape(-1, 1, 2)
    initial = None if initial is None else np.asarray(initial, np.float32).reshape(-1, 1, 2)
    flags = 0 if initial is None else cv2.OPTFLOW_USE_INITIAL_FLOW
    tracked, status, _ = cv2.calcOpticalFlowPyrLK(
        previous, current, source, initial, winSize=(21, 21), maxLevel=3, flags=flags)
    back, reverse, _ = cv2.calcOpticalFlowPyrLK(
        current, previous, tracked, None, winSize=(21, 21), maxLevel=3)
    fb = np.linalg.norm(back - source, axis=2).ravel()
    good = status.ravel().astype(bool) & reverse.ravel().astype(bool) & (fb < .5)
    return tracked.reshape(-1, 2), good, fb


def current_pose(previous_gray, gray, previous, obj, camera, cell, parity, initial_axis,
                 delta_deg):
    """Track surviving labels; reject non-checkerboard or inconsistent correspondences."""
    pr, pt = np.asarray(previous["R"]), np.asarray(previous["t_mm"])
    direction = np.asarray(initial_axis["axis_direction_camera"])
    centre = np.asarray(initial_axis["axis_point_on_initial_paper_mm"])
    q = cv2.Rodrigues(direction * np.radians(delta_deg))[0]
    predicted = project(obj, q @ pr, q @ (pt - centre) + centre, camera)
    tracked, tracked_good, fb = lk(previous_gray, gray, previous["pixels"], predicted)
    # Guided re-detection is limited to a3px subpixel window around the rigid
    # prediction. It must independently pass checker parity, RANSAC and holdout.
    was_visible = np.asarray(previous.get("visible", np.ones(len(obj), bool)), bool)
    candidate = predicted.copy()
    use_track = tracked_good & was_visible
    candidate[use_track] = tracked[use_track]
    candidate = refine(gray, candidate, 3)
    spatial = np.linalg.norm(candidate - predicted, axis=1) < 3.
    k, d = np.asarray(camera["K"], float), np.asarray(camera["dist_coeffs"], float)
    eligible = np.flatnonzero(spatial)
    if len(eligible) < 12:
        raise ValueError("Fewer than12 plausible labelled target corners")
    ok, rv, tv, inliers = cv2.solvePnPRansac(
        obj[eligible].astype(float), candidate[eligible].astype(float), k, d,
        iterationsCount=200, reprojectionError=1., confidence=.999,
        flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok or inliers is None:
        raise ValueError("Current target RANSAC PnP failed")
    r = cv2.Rodrigues(rv)[0]
    polarity, contrast = quadrant_descriptor(gray, obj, candidate, r, tv.ravel(), camera, cell)
    checker = (polarity * parity > 0) & (contrast >= 25) & np.isfinite(contrast)
    visible = np.zeros(len(obj), bool)
    visible[eligible[inliers.ravel()]] = True
    visible &= checker & spatial
    indices = np.flatnonzero(visible)
    if len(indices) < 12:
        raise ValueError("Fewer than12 verified black/white checker corners; object edges excluded")
    xy = obj[indices, :2]
    span = np.ptp(xy, axis=0)
    hull_area = float(cv2.contourArea(cv2.convexHull(xy.astype(np.float32))))
    total_area = 8 * cell[0] * 7 * cell[1]
    if span[0] < 4 * cell[0] or span[1] < 3 * cell[1] or hull_area < .25 * total_area:
        raise ValueError("Visible corner support lacks two-dimensional target span")
    # Deterministic spatial-label split after photometric/outlier exclusion.
    held = indices[::3]
    fitted = np.asarray([i for i in indices if i not in set(held)], int)
    r, t, fit_errors = pose(obj[fitted], candidate[fitted], camera)
    errors = np.linalg.norm(project(obj, r, t, camera) - candidate, axis=1)
    if rms(fit_errors) > 1 or rms(errors[held]) > 1:
        raise ValueError("Visible target PnP fit or held-out corners exceed1px RMS")
    return {
        "R": r.tolist(), "t_mm": t.tolist(), "pixels": candidate.tolist(),
        "visible": visible.tolist(), "visible_indices": indices.tolist(),
        "visible_corner_count": len(indices), "pose_fit_indices": fitted.tolist(),
        "pose_holdout_indices": held.tolist(), "fit_reprojection_rms_px": rms(fit_errors),
        "holdout_reprojection_rms_px": rms(errors[held]), "support_hull_fraction": hull_area/total_area,
        "tracked_indices": np.flatnonzero(use_track & visible).tolist(),
        "guided_reacquired_indices": np.flatnonzero(~use_track & visible).tolist(),
        "checker_contrast_by_index": contrast.tolist(),
        "forward_backward_error_px": fb.tolist(),
    }


def axis_residuals(poses, absolute, obj, axis):
    initial_r, initial_t = np.asarray(axis["initial_board_R"]), np.asarray(axis["initial_board_t_mm"])
    direction, centre = np.asarray(axis["axis_direction_camera"]), np.asarray(axis["axis_point_on_initial_paper_mm"])
    result = []
    for i, (p, motor) in enumerate(zip(poses, absolute)):
        visible = np.asarray(p["visible_indices"], int)
        local = obj[visible]
        delta = motor - axis["motor_zero_reference_deg"]
        q = cv2.Rodrigues(direction * np.radians(delta))[0]
        observed = local @ np.asarray(p["R"]).T + p["t_mm"]
        predicted = (local @ initial_r.T + initial_t - centre) @ q.T + centre
        error = np.linalg.norm(observed - predicted, axis=1)
        rotation_error = np.linalg.norm(cv2.Rodrigues(np.asarray(p["R"]) @ (q @ initial_r).T)[0])
        result.append({
            "observation_index": i, "visible_corner_count": len(visible),
            "motor_relative_deg": float(delta), "corner_xyz_rms_mm": rms(error),
            "corner_xyz_p95_mm": float(np.percentile(error, 95)),
            "orientation_error_deg": float(np.degrees(rotation_error)),
            "residual_support": "only visibly verified labelled corners at this pose",
        })
    return result


def fit_visible_axis(poses, absolute, obj, fit_indices):
    r0, t0 = np.asarray(poses[0]["R"]), np.asarray(poses[0]["t_mm"])
    relative = np.asarray(absolute) - absolute[0]
    vectors = [
        cv2.Rodrigues(np.asarray(poses[i]["R"]) @ r0.T)[0].ravel() / np.radians(relative[i])
        for i in fit_indices if abs(relative[i]) >= 1]
    if len(vectors) < 2 or np.ptp(relative[fit_indices]) < 30:
        raise ValueError("Axis requires multiple nonzero angles spanning30deg")
    _, _, vt = np.linalg.svd(vectors, full_matrices=False)
    direction = vt[0]
    if direction @ np.mean(vectors, axis=0) < 0:
        direction = -direction
    if abs(direction @ r0[:, 2]) < .5:
        raise ValueError("Axis is nearly parallel to the reference board")
    matrices, values = [], []
    # Use each pose's actually visible target centroid; no hidden-corner residuals.
    for i in fit_indices:
        if abs(relative[i]) < 1:
            continue
        loc = obj[np.asarray(poses[i]["visible_indices"], int)].mean(axis=0)
        observed = np.asarray(poses[i]["R"]) @ loc + poses[i]["t_mm"]
        initial = r0 @ loc + t0
        q = cv2.Rodrigues(direction * np.radians(relative[i]))[0]
        matrices.append(np.eye(3) - q)
        values.append(observed - q @ initial)
    matrices.append(r0[:, 2].reshape(1, 3))
    values.append(np.array([r0[:, 2] @ t0]))
    matrix, rhs = np.vstack(matrices), np.concatenate(values)
    centre, _, rank, singular = np.linalg.lstsq(matrix, rhs, rcond=None)
    if rank != 3:
        raise ValueError("Axis centre is underdetermined")
    xaxis = r0[:, 0] - direction * (direction @ r0[:, 0])
    xaxis /= np.linalg.norm(xaxis)
    return {
        "axis_direction_camera": direction.tolist(),
        "axis_point_on_initial_paper_mm": centre.tolist(),
        "initial_board_R": r0.tolist(), "initial_board_t_mm": t0.tolist(),
        "motor_zero_reference_deg": float(absolute[0]),
        "centre_condition_number": float(singular[0] / singular[-1]),
        "turntable_basis_camera_columns": np.column_stack([
            xaxis, np.cross(direction, xaxis), direction]).tolist(),
        "candidate_checks_passed": False, "large_angle_axis_validation_passed": False,
    }


def check_axis(axis, residuals, fit_indices, holdout_indices):
    fit = set(fit_indices)
    holding = set(holdout_indices)
    return {
        "fit_visible_corner_rms_le_0_75mm": all(residuals[i]["corner_xyz_rms_mm"] <= .75 for i in fit),
        "heldout_20_40_corner_rms_le_0_75mm": all(residuals[i]["corner_xyz_rms_mm"] <= .75 for i in holding),
        "all_other_observed_corner_rms_le_0_75mm": all(
            r["corner_xyz_rms_mm"] <= .75 for i, r in enumerate(residuals) if i not in fit | holding),
        "all_orientation_error_le_1deg": all(r["orientation_error_deg"] <= 1 for r in residuals),
        "centre_condition_le_20": axis["centre_condition_number"] <= 20,
    }


def validate(args):
    model = load(args.geometry)
    if not model.get("shape_preview_candidate_usable"):
        raise ValueError("Initial current geometry failed its preview checks")
    if args.execution_role == "zynq" and model.get("execution_role") != "zynq":
        raise ValueError("Formal board validation requires board-computed initial geometry")
    scan = args.scan.resolve()
    completion = load(scan / "completion.json")
    if (completion.get("status") != "completed" or completion.get("simulation") is not False
            or not completion.get("real_hardware_acquisition_verified")
            or not completion.get("return_to_origin_completed")):
        raise ValueError("Completed real scan with return-origin recapture required")
    reference_relative = model.get("reference_image", "preflight/images/bright_before_01.png")
    reference_path = args.source_images / reference_relative
    if digest(reference_path) != model["input_sha256"][reference_relative]:
        raise ValueError("Geometry reference photograph identity differs")
    original = cv2.cvtColor(read_image(reference_path), cv2.COLOR_BGR2GRAY)
    board, camera = model["current_board"], model["camera"]
    cell = [board["width_8_intervals_mm"]/8., board["height_7_intervals_mm"]/7.]
    seeds = board["corners"]
    obj = np.array([[s["column"]*cell[0], s["row"]*cell[1], 0] for s in seeds], float)
    previous = {"R": board["R"], "t_mm": board["t_mm"],
                "pixels": [s["measured_pixel"] for s in seeds], "visible": [True]*len(obj)}
    parity, contrast = quadrant_descriptor(
        original, obj, np.asarray(previous["pixels"]), np.asarray(board["R"]),
        np.asarray(board["t_mm"]), camera, cell)
    if not (contrast >= 25).all():
        raise ValueError("Original labelled reference fails checkerboard parity evidence")
    marker_pixels = np.concatenate([np.asarray(p) for _, p in sorted(
        model["current_base"]["marker_corners_px"].items())])
    old_origin = float(model["axis"]["motor_zero_reference_deg"])
    entries = []
    for point in completion["points"]:
        if point["status"] != "completed":
            raise ValueError("Incomplete scan point")
        entries.append({
            "index": point["index"], "requested_deg": point["global_angle_deg"],
            "absolute_motor_deg": point["motor_position_before_capture_deg"],
            "after_motor_deg": point["motor_position_after_capture_deg"],
            "folder": "point_%03d/images" % point["index"],
        })
    returned = completion["return_origin_capture"]
    entries.append({
        "index": "return_origin", "requested_deg": 0,
        "absolute_motor_deg": returned["motor_position_before_capture_deg"],
        "after_motor_deg": returned["motor_position_after_capture_deg"],
        "folder": "return_origin/images",
    })
    poses, absolute, fingerprints, base_checks = [], [], {}, []
    fingerprints["completion.json"] = digest(scan / "completion.json")
    previous_gray, previous_motor = original, old_origin
    first_gray = first_pose = None
    for entry in entries:
        folder = scan / entry["folder"]
        capture = load(folder / "capture.json")
        path = folder / "bright_before_01.png"
        records = [r for r in capture["bright_frames"] if r["file"] == path.name]
        if len(records) != 1 or digest(path) != records[0]["sha256"]:
            raise ValueError("Middle bright original image identity mismatch")
        if capture.get("status") != "completed" or abs(entry["after_motor_deg"]-entry["absolute_motor_deg"]) > .1:
            raise ValueError("Incomplete or moving camera pose")
        for p in [folder / "capture.json", path]:
            fingerprints[str(p.relative_to(scan))] = digest(p)
        gray = cv2.cvtColor(read_image(path), cv2.COLOR_BGR2GRAY)
        base, good, fb = lk(original, gray, marker_pixels)
        refined_base = []
        for start in range(0, len(marker_pixels), 4):
            square = marker_pixels[start:start+4]
            module = np.linalg.norm(np.roll(square, -1, axis=0)-square, axis=1).mean()/6.
            window = max(1, min(5, int(np.floor(module*.3+.5))))
            refined_base.extend(refine(gray, base[start:start+4], window))
        base = np.asarray(refined_base)
        drift = np.linalg.norm(base-marker_pixels, axis=1)
        base_record = {"index": entry["index"], "all_24_tracking_valid": bool(good.all()),
                       "median_drift_px": float(np.median(drift)), "max_drift_px": float(drift.max()),
                       "max_forward_backward_error_px": float(fb.max())}
        base_checks.append(base_record)
        base_record["stability_gate_passed"] = bool(
            good.all() and np.median(drift) <= 1. and drift.max() <= 3.)
        if entry["index"] == "return_origin":
            previous_gray, previous = first_gray, first_pose
            previous_motor = absolute[0]
        delta = entry["absolute_motor_deg"]-previous_motor
        if abs(delta) > 5.6:
            raise ValueError("Adjacent pose change exceeds bounded5deg tracking step")
        try:
            p = current_pose(previous_gray, gray, previous, obj, camera, cell, parity,
                             model["axis"], delta)
        except ValueError as exc:
            report = {
                "status": "failed", "error": str(exc), "failed_entry": entry,
                "execution_role": args.execution_role, "execution_machine": platform.machine(),
                "script_sha256": digest(__file__), "initial_geometry_sha256": digest(args.geometry),
                "hardware_accessed": False, "object_XYZ_computed": False,
                "poses": poses, "base_checks": base_checks,
                "old_axis_all_angle_residuals": axis_residuals(poses, absolute, obj, model["axis"]),
                "input_sha256": fingerprints,
            }
            write_new(args.output / "axis_validation.json", report)
            return report
        p["scan_entry"] = entry
        p["image_sha256"] = digest(path)
        poses.append(p)
        absolute.append(float(entry["absolute_motor_deg"]))
        if first_gray is None:
            first_gray, first_pose = gray, p
        previous_gray, previous, previous_motor = gray, p, absolute[-1]
    lookup = {e["requested_deg"]: i for i, e in enumerate(entries[:-1])}
    fit_indices = [lookup[a] for a in [0, 10, 30, 50, 60]]
    hold_indices = [lookup[a] for a in [20, 40]]
    old_residuals = axis_residuals(poses, absolute, obj, model["axis"])
    axis = fit_visible_axis(poses, absolute, obj, fit_indices)
    new_residuals = axis_residuals(poses, absolute, obj, axis)
    gates = check_axis(axis, new_residuals, fit_indices, hold_indices)
    gates["fixed_base_median_drift_le_1px"] = all(
        row["stability_gate_passed"] for row in base_checks)
    passed = all(gates.values())
    axis.update(candidate_checks_passed=passed, large_angle_axis_validation_passed=passed,
                candidate_check_scope="current observed0..60deg plus return; only labelled target corners",
                fit_indices=fit_indices, holdout_indices=hold_indices,
                gates=gates, residuals=new_residuals)
    report = {
        "status": "passed" if passed else "failed", "execution_role": args.execution_role,
        "execution_machine": platform.machine(), "opencv_version": cv2.__version__,
        "script_sha256": digest(__file__), "initial_geometry_sha256": digest(args.geometry),
        "hardware_accessed": False, "object_XYZ_computed": False,
        "metric_accuracy_accepted": False, "poses": poses, "base_checks": base_checks,
        "old_small_angle_axis": model["axis"], "old_axis_all_angle_residuals": old_residuals,
        "new_axis": axis, "input_sha256": fingerprints,
        "laser_plane_refitted": False, "intrinsics_refitted": False,
    }
    write_new(args.output / "axis_validation.json", report)
    if passed:
        derived = copy.deepcopy(model)
        derived.update(axis=axis, axis_validation=report, execution_role=args.execution_role,
                       execution_machine=platform.machine(), geometry_status="shape_preview_candidate",
                       metric_accuracy_accepted=False,
                       current_axis_input_sha256=fingerprints)
        # Reference plane remains independently observed at previous phase; no
        # height correction is applied when the new axis establishes its zero.
        write_new(args.output / "derived_geometry.json", derived)
    return report


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", required=True, type=Path)
    parser.add_argument("--scan", required=True, type=Path)
    parser.add_argument("--source-images", required=True, type=Path,
                        help="Source image root used by the initial current geometry")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execution-role", choices=["development", "zynq"], default="development")
    args = parser.parse_args()
    if args.execution_role == "zynq" and not platform.machine().lower().startswith(("arm", "aarch")):
        parser.error("Formal execution requires an ARM Zynq host")
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        report = validate(args)
    except Exception as exc:
        report = {"status": "failed", "error": str(exc), "traceback": traceback.format_exc(),
                  "execution_role": args.execution_role, "execution_machine": platform.machine(),
                  "hardware_accessed": False, "object_XYZ_computed": False}
        write_new(args.output / "failure.json", report)
    print(json.dumps({"status": report["status"], "error": report.get("error"),
                      "output": str(args.output)}, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
