"""Current-image target-only geometry; no device access or historical extrinsics.

Python 3 / NumPy / OpenCV 4.5 compatible. The observations file contains only
image-bound 2-D corner identities, target scales, ROIs, and declared fit/holdout
groups. Calibration and PLY reconstruction can run entirely on Zynq PS.
Outputs are shape previews, never accepted dimensional measurements.
"""
import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "04_runtime"))
from geometric_calibration import camera_arrays, pixel_rays
from geometry_core import intersect_rays
from scan_stripe_quality import common_stripe_pixels


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_image(path):
    im = cv2.imdecode(np.frombuffer(Path(path).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    if im is None or im.shape != (480, 640, 3):
        raise ValueError("Original 640x480 image required: " + str(path))
    return im


def rms(values):
    return float(np.sqrt(np.mean(np.asarray(values, float) ** 2)))


def refine(gray, points, window=3):
    points = np.asarray(points, np.float32).reshape(-1, 1, 2)
    return cv2.cornerSubPix(
        gray, points.copy(), (window, window), (-1, -1),
        (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 50, .001),
    ).reshape(-1, 2)


def pose(obj, pixels, camera):
    k, d = camera_arrays(camera)
    ok, rv, tv = cv2.solvePnP(
        np.asarray(obj, float), np.asarray(pixels, float), k, d,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        raise ValueError("Current target PnP failed")
    rot = cv2.Rodrigues(rv)[0]
    if ((np.asarray(obj) @ rot.T + tv.ravel())[:, 2] <= 0).any():
        raise ValueError("Target PnP behind camera")
    projected = cv2.projectPoints(np.asarray(obj, float), rv, tv, k, d)[0].reshape(-1, 2)
    err = np.linalg.norm(projected - np.asarray(pixels).reshape(-1, 2), axis=1)
    return rot, tv.ravel(), err


def markers(image, observations):
    """Prefer on-board decoding; image-bound annotated corners are fallback."""
    aruco = getattr(cv2, "aruco", None)
    mode = observations.get("_marker_corner_mode", "auto")
    if mode not in ("auto", "annotated-adaptive"):
        raise ValueError("Unknown explicit marker observation mode")
    if aruco is not None and mode == "auto":
        dictionary = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
        if hasattr(aruco, "DetectorParameters"):
            params = aruco.DetectorParameters()
        else:
            params = aruco.DetectorParameters_create()
        params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
        if hasattr(aruco, "ArucoDetector"):
            corners, ids, _ = aruco.ArucoDetector(dictionary, params).detectMarkers(image)
        else:
            corners, ids, _ = aruco.detectMarkers(image, dictionary, parameters=params)
        if ids is not None:
            found = {int(i): c.reshape(4, 2) for i, c in zip(ids.ravel(), corners)}
            needed = observations["marker_fit_ids"] + observations["marker_holdout_ids"]
            if all(i in found for i in needed):
                return found, "decoded_on_this_host"
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    found = {}
    for ident, raw in observations["annotated_marker_corners"].items():
        seed = np.asarray(raw, float)
        # A4x4 has four code modules plus two black-border modules per side.
        # OpenCV documents a window bounded by0.3 times the mean module size.
        # Fixed3px windows can capture an adjacent internal code corner here.
        module_px = np.linalg.norm(np.roll(seed, -1, axis=0) - seed, axis=1).mean() / 6.
        window = max(1, min(5, int(np.floor(module_px * .3 + .5))))
        measured = refine(gray, seed, window)
        if np.linalg.norm(measured - seed, axis=1).max() > 1.5:
            raise ValueError("Annotated marker refinement drift exceeds1.5px")
        found[int(ident)] = measured
    return found, "image_bound_2D_annotations_adaptive_window_refined_on_this_host"


def solve_base_plane(rays, size, initial):
    """Plane a dot XYZ=1 fitted from square edges/diagonals, no object data."""
    def residual(a):
        denom = rays @ a
        if (denom <= 0).any():
            return np.full((len(rays) // 4) * 6, 1e6)
        xyz = (rays / denom[:, None]).reshape(-1, 4, 3)
        edges = np.linalg.norm(np.roll(xyz, -1, axis=1) - xyz, axis=2) / size - 1
        diagonals = np.stack([
            np.linalg.norm(xyz[:, 2] - xyz[:, 0], axis=1),
            np.linalg.norm(xyz[:, 3] - xyz[:, 1], axis=1),
        ], axis=1) / (size * np.sqrt(2)) - 1
        return np.column_stack([edges, diagonals]).ravel()
    a = np.asarray(initial, float).copy()
    damping = 1e-3
    for _ in range(100):
        f = residual(a)
        jac = np.empty((len(f), 3))
        for j in range(3):
            step = max(abs(a[j]) * 1e-5, 1e-8)
            trial = a.copy()
            trial[j] += step
            jac[:, j] = (residual(trial) - f) / step
        normal = jac.T @ jac
        delta = np.linalg.solve(
            normal + damping * np.diag(np.maximum(np.diag(normal), 1)),
            -jac.T @ f,
        )
        if np.linalg.norm(residual(a + delta)) < np.linalg.norm(f):
            a += delta
            damping = max(damping * .3, 1e-10)
            if np.linalg.norm(delta) < 1e-12:
                break
        else:
            damping = min(damping * 10, 1e12)
    return a, residual(a)


def target_planes(image, camera, obs, size, width, height):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    seeds = obs["checkerboard_corners"]
    if len({(s["row"], s["column"]) for s in seeds}) != len(seeds):
        raise ValueError("Duplicate annotated checkerboard identities")
    pix = refine(gray, [s["pixel"] for s in seeds])
    obj = np.array([[s["column"] * width / 8., s["row"] * height / 7., 0]
                    for s in seeds], float)
    fit = np.asarray(obs["checkerboard_fit_indices"], int)
    hold = np.asarray(obs["checkerboard_holdout_indices"], int)
    if set(fit) & set(hold) or len(hold) < 8:
        raise ValueError("Independent visible checkerboard holdout required")
    rot, trans, _ = pose(obj[fit], pix[fit], camera)
    k, d = camera_arrays(camera)
    rv = cv2.Rodrigues(rot)[0]
    projected = cv2.projectPoints(obj, rv, trans, k, d)[0].reshape(-1, 2)
    err = np.linalg.norm(projected - pix, axis=1)
    board = {
        "normal": rot[:, 2].tolist(), "offset_mm": float(-rot[:, 2] @ trans),
        "R": rot.tolist(), "t_mm": trans.tolist(), "width_8_intervals_mm": width,
        "height_7_intervals_mm": height, "fit_reprojection_rms_px": rms(err[fit]),
        "holdout_reprojection_rms_px": rms(err[hold]),
        "corners": [dict(s, measured_pixel=p.tolist(), reprojection_error_px=float(e))
                    for s, p, e in zip(seeds, pix, err)],
    }
    found, method = markers(image, obs)
    fit_ids, hold_ids = obs["marker_fit_ids"], obs["marker_holdout_ids"]
    if set(fit_ids) & set(hold_ids) or len(fit_ids) < 3 or not hold_ids:
        raise ValueError("Independent marker holdout required")
    square = np.array([[0, 0, 0], [size, 0, 0], [size, size, 0], [0, size, 0]], float)
    initials = []
    # Holdout marker corners never initialize or optimize the base plane.
    for ident in fit_ids:
        mr, mt, _ = pose(square, found[ident], camera)
        n = mr[:, 2]
        initials.append(n / float(n @ mt))
    rays = pixel_rays(np.concatenate([found[i] for i in fit_ids]), camera)
    attempts = [solve_base_plane(rays, size, p)
                for p in initials + [np.median(initials, axis=0)]]
    a, cost = min(attempts, key=lambda pair: np.linalg.norm(pair[1]))
    normal, offset = a / np.linalg.norm(a), -1 / np.linalg.norm(a)
    edges = {}
    for ident in fit_ids + hold_ids:
        r = pixel_rays(found[ident], camera)
        xyz = r / (r @ a)[:, None]
        lengths = np.linalg.norm(np.roll(xyz, -1, axis=0) - xyz, axis=1)
        edges[str(ident)] = {
            "held_out": ident in hold_ids, "edges_mm": lengths.tolist(),
            "max_abs_error_mm": float(abs(lengths - size).max()),
        }
    base = {
        "normal": normal.tolist(), "offset_mm": float(offset),
        "nominal_marker_black_size_mm": size, "physical_scale_remeasured": False,
        "marker_observation_method": method, "fit_marker_ids": fit_ids,
        "holdout_marker_ids": hold_ids, "edge_checks": edges,
        "dimensionless_fit_RMS": rms(cost),
        "marker_corners_px": {str(i): found[i].tolist() for i in fit_ids + hold_ids},
        "marker_corner_mode": obs.get("_marker_corner_mode", "auto"),
    }
    return board, base, obj, pix, gray


def laser_groups(root, camera, obs, board, base, fingerprints):
    groups = []
    for item in obs["laser_groups"]:
        paths = [root / p for p in item["images"]]
        capture_path = paths[0].parent / "capture.json"
        capture = load(capture_path)
        fingerprints[str(capture_path.relative_to(root))] = digest(capture_path)
        if (capture.get("status") != "completed"
                or not capture.get("ae_exposure_restoration_verified")
                or capture.get("restore_errors") or not capture.get("camera_release_completed")):
            raise ValueError("Laser capture/restoration evidence failed")
        by_file = {f["file"]: f for f in capture["frames"]}
        for path in paths:
            record = by_file[path.name]
            controls = record["controls_readback"]
            if (controls["ae"] != 1 or controls["exp"] != item["exposure"]
                    or record["sha256"] != digest(path)):
                raise ValueError("Laser capture exposure/image identity failed")
        ims = [read_image(p) for p in paths]
        for p in paths:
            fingerprints[str(p.relative_to(root))] = digest(p)
        pixels, quality = common_stripe_pixels(paths, item["roi"])
        red = []
        for x, y in pixels:
            vals = [im[int(round(y)), int(x)].astype(int) for im in ims]
            red.append(all(int(bgr[2]) - max(int(bgr[0]), int(bgr[1])) >= 12
                           for bgr in vals))
        red = np.asarray(red, bool)
        accepted = pixels[red]
        target = board if item["plane"] == "board" else base
        if len(accepted):
            xyz, valid = intersect_rays(pixel_rays(accepted, camera),
                                        target["normal"], target["offset_mm"])
            if not valid.all():
                raise ValueError("Target laser ray intersection invalid")
        else:
            xyz = np.empty((0, 3))
        groups.append({
            **item, "quality": quality, "true_red_columns": int(red.sum()),
            "pixels": accepted.tolist(), "XYZ_mm": xyz.tolist(),
        })
    return groups


def laser_model(groups, obs):
    by_name = {g["name"]: g for g in groups}
    fitting = obs["laser_fit_groups"]
    holding = obs["laser_holdout_groups"]
    if set(fitting) & set(holding):
        raise ValueError("Laser holdout overlaps fit groups")
    for name in fitting + holding:
        g = by_name[name]
        if not g["quality"]["image_quality_gate_passed"] or g["true_red_columns"] < 20:
            raise ValueError("Declared laser group fails original quality gate: " + name)
    if {by_name[n]["plane"] for n in fitting} != {"board", "base"}:
        raise ValueError("Two separately observed physical planes required")
    points = np.concatenate([by_name[n]["XYZ_mm"] for n in fitting])
    centre = points.mean(axis=0)
    _, singular, vectors = np.linalg.svd(points - centre, full_matrices=False)
    normal = vectors[-1]
    offset = float(-normal @ centre)
    checks = {}
    for g in groups:
        if g["true_red_columns"] >= 20:
            residual = np.asarray(g["XYZ_mm"]) @ normal + offset
            checks[g["name"]] = {
                "used_for_fit": g["name"] in fitting,
                "declared_holdout": g["name"] in holding,
                "count": len(residual), "signed_median_mm": float(np.median(residual)),
                "rms_mm": rms(residual), "absolute_p95_mm": float(np.percentile(abs(residual), 95)),
            }
    spread = singular / np.sqrt(len(points))
    return {
        "normal": normal.tolist(), "offset_mm": offset,
        "fit_group_names": fitting, "holdout_group_names": holding,
        "spread_mm": spread.tolist(), "fit_rms_mm": float(spread[-1]),
        "condition_largest_to_second": float(spread[0] / spread[1]),
        "checks": checks, "independent_third_plane_checked": False,
    }


def rotation_axis(root, camera, obs, obj, pixels, reference_gray, fingerprints):
    completion_path = root / obs["motor_completion"]
    completion = load(completion_path)
    fingerprints[obs["motor_completion"]] = digest(completion_path)
    groups = []
    for point in completion["points"]:
        index = point["index"]
        frames = []
        for frame in range(3):
            rel = "motor_test/point_%02d/raw_%02d.png" % (index, frame)
            path = root / rel
            fingerprints[rel] = digest(path)
            image = cv2.cvtColor(read_image(path), cv2.COLOR_BGR2GRAY)
            source = pixels.astype(np.float32).reshape(-1, 1, 2)
            tracked, status, _ = cv2.calcOpticalFlowPyrLK(
                reference_gray, image, source, None, winSize=(21, 21), maxLevel=3)
            back, reverse_status, _ = cv2.calcOpticalFlowPyrLK(
                image, reference_gray, tracked, None, winSize=(21, 21), maxLevel=3)
            fb = np.linalg.norm(back - source, axis=2).ravel()
            if not (status.all() and reverse_status.all()) or fb.max() > .5:
                raise ValueError("Current target tracking failed: " + rel)
            measured = refine(image, tracked)
            rot, trans, errors = pose(obj, measured, camera)
            frames.append({
                "image": rel, "R": rot.tolist(), "t_mm": trans.tolist(),
                "pixels": measured.tolist(), "reprojection_rms_px": rms(errors),
                "max_forward_backward_error_px": float(fb.max()),
            })
        groups.append({
            "index": index, "motor_relative_deg": point["feedback_relative_deg"],
            "motor_before_deg": point["before_deg"], "motor_after_deg": point["after_deg"],
            "frames": frames, "fixed_selection": "middle_frame",
        })
    if len(groups) != 3:
        raise ValueError("This candidate requires current zero/positive/return observations")
    p0, p1 = groups[0]["frames"][1], groups[1]["frames"][1]
    r0, r1 = np.asarray(p0["R"]), np.asarray(p1["R"])
    t0, t1 = np.asarray(p0["t_mm"]), np.asarray(p1["t_mm"])
    angle = float(groups[1]["motor_relative_deg"] - groups[0]["motor_relative_deg"])
    if not 1 < angle < 15:
        raise ValueError("Expected the documented small positive motor test")
    vector = cv2.Rodrigues(r1 @ r0.T)[0].ravel()
    direction = vector / np.linalg.norm(vector)
    q = cv2.Rodrigues(direction * np.radians(angle))[0]
    n0 = r0[:, 2]
    matrix = np.vstack([np.eye(3) - q, n0.reshape(1, 3)])
    values = np.r_[t1 - q @ t0, n0 @ t0]
    centre, _, rank, singular = np.linalg.lstsq(matrix, values, rcond=None)
    if rank != 3:
        raise ValueError("Current rotation axis underdetermined")
    checks = []
    for group in groups:
        p = group["frames"][1]
        measured_r, measured_t = np.asarray(p["R"]), np.asarray(p["t_mm"])
        delta = group["motor_relative_deg"] - groups[0]["motor_relative_deg"]
        q = cv2.Rodrigues(direction * np.radians(delta))[0]
        observed = obj @ measured_r.T + measured_t
        predicted = (obj @ r0.T + t0 - centre) @ q.T + centre
        checks.append({
            "point_index": group["index"], "fit": group["index"] in [0, 1],
            "corner_xyz_rms_mm": rms(np.linalg.norm(predicted - observed, axis=1)),
            "orientation_error_deg": float(np.degrees(np.linalg.norm(
                cv2.Rodrigues(measured_r @ (q @ r0).T)[0]))),
        })
    return {
        "axis_direction_camera": direction.tolist(),
        "axis_point_on_initial_paper_mm": centre.tolist(),
        "initial_board_R": r0.tolist(), "initial_board_t_mm": t0.tolist(),
        "motor_fit_angle_deg": angle,
        "motor_zero_reference_deg": groups[0]["motor_before_deg"],
        "optical_fit_angle_deg": float(np.degrees(np.linalg.norm(vector))),
        "centre_condition_number": float(singular[0] / singular[-1]),
        "observations": groups, "checks": checks,
        "relative_zero": "current motor_test point_00; no historical absolute zero",
        "new_scan_angle_rule": "measured_motor_position_deg - motor_zero_reference_deg",
        "large_angle_axis_validation_passed": False,
        "warning": "Only one nonzero pose near 5 degrees; wide-angle axis accuracy unvalidated.",
    }


def calibrate(args):
    root = args.source_images.resolve()
    obs = load(args.observations)
    obs["_marker_corner_mode"] = getattr(args, "marker_corner_mode", "auto")
    image_path = root / obs["reference_image"]
    if digest(image_path) != obs["reference_image_sha256"]:
        raise ValueError("2D annotations belong to a different original image")
    if digest(args.camera) != obs["intrinsic_source_sha256"]:
        raise ValueError("Intrinsic source differs from the annotated image manifest")
    for relative, expected in obs["input_sha256"].items():
        path = root / relative
        if path.resolve().parent != root and root not in path.resolve().parents:
            raise ValueError("Manifest source escapes source_images")
        if digest(path) != expected:
            raise ValueError("Original observation fingerprint mismatch: " + relative)
    camera = load(args.camera)
    image = read_image(image_path)
    fingerprints = {obs["reference_image"]: digest(image_path)}
    board, base, obj, pixels, gray = target_planes(
        image, camera, obs, args.marker_size_mm,
        args.board_width_mm, args.board_height_mm)
    groups = laser_groups(root, camera, obs, board, base, fingerprints)
    laser = laser_model(groups, obs)
    axis = rotation_axis(root, camera, obs, obj, pixels, gray, fingerprints)
    gates = {
        "board_fit_and_holdout_below_1px": max(
            board["fit_reprojection_rms_px"], board["holdout_reprojection_rms_px"]) < 1,
        "base_fit_relative_rms_below_3percent": base["dimensionless_fit_RMS"] < .03,
        "base_holdout_edge_error_below_10percent": all(
            base["edge_checks"][str(i)]["max_abs_error_mm"] / args.marker_size_mm < .1
            for i in base["holdout_marker_ids"]),
        "laser_second_spread_at_least_5mm": laser["spread_mm"][1] >= 5,
        "laser_fit_rms_below_0_5mm": laser["fit_rms_mm"] < .5,
        "declared_laser_holdout_rms_below_1_5mm": all(
            laser["checks"][name]["rms_mm"] < 1.5 for name in obs["laser_holdout_groups"]),
        "motor_optical_agreement_below_0_5deg": abs(
            axis["motor_fit_angle_deg"] - axis["optical_fit_angle_deg"]) < .5,
        "axis_return_corner_rms_below_0_75mm": axis["checks"][2]["corner_xyz_rms_mm"] < .75,
        "axis_centre_condition_below_20": axis["centre_condition_number"] < 20,
    }
    direction = np.asarray(axis["axis_direction_camera"])
    initial_r = np.asarray(axis["initial_board_R"])
    xaxis = initial_r[:, 0] - direction * (direction @ initial_r[:, 0])
    xaxis /= np.linalg.norm(xaxis)
    axis["turntable_basis_camera_columns"] = np.column_stack([
        xaxis, np.cross(direction, xaxis), direction]).tolist()
    axis["candidate_checks_passed"] = all(gates.values())
    axis["candidate_check_scope"] = "current small-angle shape preview only"
    ref_normal = initial_r[:, 2]
    ref_offset = float(-ref_normal @ np.asarray(axis["initial_board_t_mm"]))
    if ref_normal @ (-np.asarray(axis["initial_board_t_mm"])) < 0:
        ref_normal, ref_offset = -ref_normal, -ref_offset
    reference = {"normal": ref_normal.tolist(), "offset_mm": ref_offset,
                 "source_image": "motor_test/point_00/raw_01.png",
                 "kind": "current visible measured checkerboard plane at relative zero"}
    report = {
        "kind": "current_target_only_shape_preview_geometry",
        "geometry_status": "shape_preview_candidate",
        "execution_role": args.execution_role, "execution_machine": platform.machine(),
        "opencv_version": cv2.__version__,
        "calibration_algorithm_version": "current_targets_v2_adaptive_annotation",
        "metric_accuracy_accepted": False, "object_used_in_geometry_fit": False,
        "historical_extrinsics_used": False, "hardware_accessed": False,
        "intrinsic_source_sha256": digest(args.camera),
        "observations_sha256": digest(args.observations),
        "script_sha256": digest(__file__), "camera": camera,
        "current_board": board, "current_base": base, "laser": laser, "axis": axis,
        "laser_plane": {"normal": laser["normal"], "offset_mm": laser["offset_mm"]},
        "reference_plane": reference,
        "target_plane_separation_at_board_origin_mm": float(abs(
            np.asarray(base["normal"]) @ board["t_mm"] + base["offset_mm"])),
        "laser_groups": groups, "input_sha256": fingerprints,
        "shape_preview_engineering_checks": gates,
        "engineering_thresholds_are_preview_checks_not_accuracy_acceptance": True,
        "shape_preview_candidate_usable": all(gates.values()),
        "limitations": [
            "Reused historical intrinsics; current target reprojection checked, new intrinsic calibration absent.",
            "22 mm marker size is printed nominal text, not physically remeasured.",
            "Holdout marker dimensional errors remain explicitly reported.",
            "No independent third laser reference plane; all diagnostic exposure failures retained.",
            "Only a 5 degree rotation baseline; full-angle axis and object coverage unvalidated.",
            "Shape preview only. No object fitting, vertical translation, or accepted metric accuracy.",
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / "current_geometry.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({
        "output": str(output), "shape_preview_candidate_usable": all(gates.values()),
        "checks": gates, "laser_fit_rms_mm": laser["fit_rms_mm"],
        "axis_optical_angle_deg": axis["optical_fit_angle_deg"],
    }, indent=2))


def reconstruct_profiles(model, profiles):
    """Board-side 3-D math from already independently accepted actual PL pixels.

    This performs no point fabrication, point-height correction, or surface fit.
    Profile selection and actual PL/quality provenance must be supplied upstream.
    """
    if not model["shape_preview_candidate_usable"]:
        raise ValueError("Current geometry failed the shape-preview engineering checks")
    axis = model["axis"]
    direction = np.asarray(axis["axis_direction_camera"], float)
    centre = np.asarray(axis["axis_point_on_initial_paper_mm"], float)
    initial_r = np.asarray(axis["initial_board_R"], float)
    xaxis = initial_r[:, 0] - direction * (direction @ initial_r[:, 0])
    xaxis /= np.linalg.norm(xaxis)
    yaxis = np.cross(direction, xaxis)
    basis = np.column_stack([xaxis, yaxis, direction])
    clouds, details = [], []
    for profile in profiles:
        if profile.get("source") != "actual_PL" or not profile.get("quality_accepted", False):
            raise ValueError("Reconstruction requires accepted actual PL profile provenance")
        pixels = np.asarray(profile["pixels"], float)
        xyz, valid = intersect_rays(
            pixel_rays(pixels, model["camera"]),
            model["laser"]["normal"], model["laser"]["offset_mm"])
        if not valid.all():
            raise ValueError("Invalid current laser intersection")
        angle = float(profile["relative_angle_deg"])
        undo_row_matrix = cv2.Rodrigues(direction * np.radians(angle))[0]
        local = ((xyz - centre) @ undo_row_matrix) @ basis
        clouds.append(local)
        details.append({"relative_angle_deg": angle, "count": len(local),
                        "source_record": profile.get("source_record")})
    if not clouds:
        raise ValueError("No accepted profiles")
    return np.concatenate(clouds), details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-images", required=True, type=Path)
    parser.add_argument("--camera", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--marker-size-mm", type=float, default=22.)
    parser.add_argument("--board-width-mm", type=float, default=77.)
    parser.add_argument("--board-height-mm", type=float, default=67.)
    parser.add_argument("--execution-role", choices=["development", "zynq"], default="development")
    parser.add_argument("--marker-corner-mode", choices=["auto", "annotated-adaptive"], default="auto")
    args = parser.parse_args()
    if args.execution_role == "zynq" and not platform.machine().lower().startswith(("arm", "aarch")):
        parser.error("Zynq execution role requires an ARM board; PC use is development-only")
    dimensions = [args.marker_size_mm, args.board_width_mm, args.board_height_mm]
    if not np.isfinite(dimensions).all() or min(dimensions) <= 0:
        parser.error("Target dimensions must be positive")
    calibrate(args)


if __name__ == "__main__":
    main()
