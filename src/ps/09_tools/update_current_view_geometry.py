"""Small current-rig camera-pose update with independent known-target checks.

Runs on saved images only. No object points, metric acceptance, height offsets,
laser refitting, camera IO, or motor IO. Formal geometry is computed on Zynq.
"""
import argparse
import copy
import json
import platform
from pathlib import Path

import cv2
import numpy as np

import calibrate_current_targets as calibration
import validate_object_axis as axis_tool


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def update(args):
    source = args.source_images.resolve()
    scan = args.scan.resolve()
    old = calibration.load(args.geometry)
    if not old["shape_preview_candidate_usable"]:
        raise ValueError("Initial current geometry must pass its original preview checks")
    if args.execution_role == "zynq" and old["execution_role"] != "zynq":
        raise ValueError("Formal update requires board-computed initial geometry")
    reference = source / "preflight/images/bright_before_01.png"
    current = source / "white_exposure20/images/bright_before_01.png"
    if calibration.digest(reference) != old["input_sha256"]["preflight/images/bright_before_01.png"]:
        raise ValueError("Original geometry reference identity mismatch")
    capture = calibration.load(current.parent / "capture.json")
    frame = next(f for f in capture["bright_frames"] if f["file"] == current.name)
    if calibration.digest(current) != frame["sha256"]:
        raise ValueError("New reference image identity mismatch")
    original = cv2.cvtColor(calibration.read_image(reference), cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(calibration.read_image(current), cv2.COLOR_BGR2GRAY)
    camera = old["camera"]
    ids = [3, 4, 5, 6, 12, 13]
    pixels = np.concatenate([old["current_base"]["marker_corners_px"][str(i)] for i in ids])
    tracked, valid, fb = axis_tool.lk(original, gray, pixels)
    if not valid.all():
        raise ValueError("Current base corner correspondence failed")
    measured = []
    for start in range(0, 24, 4):
        square = pixels[start:start+4]
        module = np.linalg.norm(np.roll(square, -1, axis=0)-square, axis=1).mean()/6.
        window = max(1, min(5, int(np.floor(module*.3+.5))))
        measured.extend(calibration.refine(gray, tracked[start:start+4], window))
    measured = np.asarray(measured)
    base = old["current_base"]
    target, valid = calibration.intersect_rays(
        calibration.pixel_rays(pixels, camera), base["normal"], base["offset_mm"])
    if not valid.all():
        raise ValueError("Initial known base target is invalid")
    # IDs3,4,5,6,12 fit; ID13 is never fitted.
    rotation, translation, fit_error = calibration.pose(target[:20], measured[:20], camera)
    projected = axis_tool.project(target, rotation, translation, camera)
    hold_error = np.linalg.norm(projected[20:]-measured[20:], axis=1)
    board = old["current_board"]
    cell = [board["width_8_intervals_mm"]/8., board["height_7_intervals_mm"]/7.]
    obj = np.array([[p["column"]*cell[0], p["row"]*cell[1], 0] for p in board["corners"]])
    prior = {"R": board["R"], "t_mm": board["t_mm"],
             "pixels": [p["measured_pixel"] for p in board["corners"]],
             "visible": [True]*len(obj)}
    parity, contrast = axis_tool.quadrant_descriptor(
        original, obj, np.asarray(prior["pixels"]), np.asarray(board["R"]),
        np.asarray(board["t_mm"]), camera, cell)
    if not (contrast >= 25).all():
        raise ValueError("Original checkerboard correspondence is not independently visible")
    observed = axis_tool.current_pose(
        original, gray, prior, obj, camera, cell, parity, old["axis"], 0.)
    visible = observed["visible_indices"]
    board_predicted = axis_tool.project(
        obj, rotation @ np.asarray(board["R"]),
        rotation @ np.asarray(board["t_mm"]) + translation, camera)
    board_error = np.linalg.norm(board_predicted[visible] -
                                 np.asarray(observed["pixels"])[visible], axis=1)
    def transport(plane):
        normal = rotation @ np.asarray(plane["normal"])
        return {"normal": normal.tolist(),
                "offset_mm": float(plane["offset_mm"]-normal @ translation)}
    new_laser = transport(old["laser_plane"])
    new_base = {**base, **transport(base),
                "marker_corners_px": {str(ident): measured[4*i:4*i+4].tolist()
                                      for i, ident in enumerate(ids)}}
    board_r, board_t = np.asarray(observed["R"]), np.asarray(observed["t_mm"])
    new_board = {
        **board, "R": board_r.tolist(), "t_mm": board_t.tolist(),
        "normal": board_r[:, 2].tolist(), "offset_mm": float(-board_r[:, 2] @ board_t),
        "corners": [
            {**board["corners"][i], "measured_pixel": observed["pixels"][i]}
            for i in visible],
        "fit_reprojection_rms_px": observed["fit_reprojection_rms_px"],
        "holdout_reprojection_rms_px": observed["holdout_reprojection_rms_px"],
    }
    fingerprints = {
        "old_geometry_json": calibration.digest(args.geometry),
        "preflight/images/bright_before_01.png": calibration.digest(reference),
        "white_exposure20/images/bright_before_01.png": calibration.digest(current),
        "scan/completion.json": calibration.digest(scan / "completion.json"),
    }
    # ROIs are physical paper/known checkerboard patches identified in bright
    # images. The original broad right ROI includes a cable/raised rig surfaces
    # and is retained as an explicitly invalid plane-assignment diagnostic.
    definitions = [
        ("board_return5", "board", scan / "return_origin/images", 5, [235, 320, 294, 355]),
        ("base_left20_paper", "base", source / "white_exposure20/images", 20, [145, 355, 205, 400]),
        ("base_right80_paper", "base", source / "white_exposure80/images", 80, [525, 440, 565, 470]),
        ("original_broad_right_includes_nonplanar_cable", "base",
         source / "white_exposure80/images", 80, [470, 405, 580, 465]),
    ]
    checks, diagnostic = {}, {}
    for name, plane_name, folder, exposure, roi in definitions:
        # laser_groups also verifies capture AE1, explicit exposure, all raw
        # hashes, restoration, frozen quality and independent true-red pixels.
        group = {"name": name, "plane": plane_name, "exposure": exposure,
                 "roi": roi, "images": ["raw_%02d.png" % i for i in range(3)]}
        local_hashes = {}
        g = calibration.laser_groups(
            folder, camera, {"laser_groups": [group]}, new_board, new_base, local_hashes)[0]
        for key, value in local_hashes.items():
            fingerprints[str((folder/key).relative_to(source))] = value
        xyz = np.asarray(g["XYZ_mm"], float)
        residual = xyz @ np.asarray(new_laser["normal"]) + new_laser["offset_mm"]
        result = {
            "count": len(xyz), "rms_mm": calibration.rms(residual) if len(xyz) else None,
            "absolute_p95_mm": float(np.percentile(abs(residual), 95)) if len(xyz) else None,
            "quality": g["quality"], "roi": roi, "true_red_columns": g["true_red_columns"],
            "physical_target": plane_name, "pixels": g["pixels"],
        }
        if name.startswith("original_broad"):
            result["excluded_reason"] = "Bright image shows cable and raised structure; not one known paper plane"
            diagnostic[name] = result
        else:
            checks[name] = result
    # All new scan views must retain the current fixed base, including return.
    completion = calibration.load(scan / "completion.json")
    stability = []
    folders = [scan / ("point_%03d/images" % p["index"]) for p in completion["points"]]
    folders.append(scan / "return_origin/images")
    for folder in folders:
        path = folder / "bright_before_01.png"
        metadata = calibration.load(folder / "capture.json")
        row = next(r for r in metadata["bright_frames"] if r["file"] == path.name)
        if calibration.digest(path) != row["sha256"]:
            raise ValueError("Scan bright image fingerprint differs")
        current_gray = cv2.cvtColor(calibration.read_image(path), cv2.COLOR_BGR2GRAY)
        q, good, reverse = axis_tool.lk(gray, current_gray, measured)
        delta = np.linalg.norm(q-measured, axis=1)
        # A rotating board can occlude a fixed base marker. Keep only entire
        # markers whose four corners independently pass the bidirectional gate;
        # do not exclude corners based on their measured displacement.
        marker_good = [bool(good[4*i:4*i+4].all()) for i in range(len(ids))]
        keep = np.repeat(marker_good, 4)
        supported = np.ptp(measured[keep], axis=0) if keep.any() else [0, 0]
        stability.append({
            "image": str(path.relative_to(scan)),
            "verified_visible_marker_ids": [i for i, ok in zip(ids, marker_good) if ok],
            "excluded_untracked_marker_ids": [i for i, ok in zip(ids, marker_good) if not ok],
            "forward_backward_error_px": reverse.tolist(),
            "visible_marker_count": sum(marker_good),
            "spans_both_base_dimensions": bool(min(supported) >= 100),
            "median_drift_px": float(np.median(delta[keep])) if keep.any() else None,
            "max_drift_px": float(delta[keep].max()) if keep.any() else None,
        })
        fingerprints[str(path.relative_to(source))] = calibration.digest(path)
    gates = {
        "base_fit_reprojection_rms_lt_1px": calibration.rms(fit_error) < 1,
        "excluded_marker13_reprojection_rms_lt_1px": calibration.rms(hold_error) < 1,
        "independent_visible_checkerboard_reprojection_rms_lt_1px": calibration.rms(board_error) < 1,
        "independent_current_laser_groups_original_quality_passed": all(
            q["quality"]["image_quality_gate_passed"] and q["count"] >= 20 for q in checks.values()),
        "all_independent_laser_rms_lt_1_5mm": all(
            q["rms_mm"] is not None and q["rms_mm"] < 1.5 for q in checks.values()),
        "current_base_scan_median_drift_le_1px": all(
            p["visible_marker_count"] >= 4 and p["spans_both_base_dimensions"]
            and p["median_drift_px"] <= 1 and p["max_drift_px"] <= 3
            for p in stability),
    }
    report = {
        "geometry_method": "current_base_rigid_transport_with_independent_target_checks",
        "gates": gates, "input_sha256": fingerprints, "laser_checks": checks,
        "rejected_plane_assignment_diagnostics": diagnostic, "current_scan_base_stability": stability,
        "base_fit_reprojection_rms_px": calibration.rms(fit_error),
        "excluded_marker13_reprojection_rms_px": calibration.rms(hold_error),
        "independent_checkerboard_reprojection_rms_px": calibration.rms(board_error),
        "rotation": rotation.tolist(), "translation_mm": translation.tolist(),
        "no_object_used": True, "no_laser_refit": True,
        "script_sha256": calibration.digest(__file__),
    }
    model = copy.deepcopy(old)
    initial_axis = copy.deepcopy(old["axis"])
    initial_axis["axis_direction_camera"] = (rotation @ np.asarray(initial_axis["axis_direction_camera"])).tolist()
    initial_axis["axis_point_on_initial_paper_mm"] = (
        rotation @ np.asarray(initial_axis["axis_point_on_initial_paper_mm"]) + translation).tolist()
    initial_axis["initial_board_R"] = board_r.tolist()
    initial_axis["initial_board_t_mm"] = board_t.tolist()
    initial_axis["motor_zero_reference_deg"] = completion["return_origin_capture"]["motor_position_after_capture_deg"]
    initial_axis["large_angle_axis_validation_passed"] = False
    model.update(
        current_board=new_board, current_base=new_base, laser_plane=new_laser,
        laser={**model["laser"], **new_laser}, axis=initial_axis,
        reference_image="white_exposure20/images/bright_before_01.png",
        input_sha256={**old["input_sha256"],
                      "white_exposure20/images/bright_before_01.png": calibration.digest(current)},
        current_view_validation=report, execution_role=args.execution_role,
        execution_machine=platform.machine(), metric_accuracy_accepted=False,
        shape_preview_candidate_usable=all(gates.values()),
        current_laser_geometry_verified_for_preview=all(gates.values()),
        geometry_status="shape_preview_candidate",
    )
    n = board_r[:, 2]
    d = -n @ board_t
    if n @ (-board_t) < 0:
        n, d = -n, -d
    model["reference_plane"] = {"normal": n.tolist(), "offset_mm": float(d),
                                "kind": "independently observed current checkerboard plane"}
    write(args.output / "current_view_validation.json", report)
    write(args.output / "current_view_geometry.json", model)
    return model


def compose_diagnostic(args):
    model = calibration.load(args.geometry)
    report = calibration.load(args.axis_report)
    validation = model["current_view_validation"]
    if not validation["gates"] or not all(validation["gates"].values()):
        raise ValueError("Current camera/laser checks failed; no diagnostic object reconstruction")
    if args.execution_role == "zynq" and (
            model.get("execution_role") != "zynq" or report.get("execution_role") != "zynq"):
        raise ValueError("Formal diagnostic requires board-computed geometry and axis report")
    axis = report["new_axis"]
    if all(axis["gates"].values()):
        raise ValueError("Use the accepted derived geometry when all axis checks pass")
    model["axis"] = axis
    model["axis"]["candidate_checks_passed"] = False
    model["axis"]["large_angle_axis_validation_passed"] = False
    model.update(diagnostic_preview_only=True, shape_preview_candidate_usable=False,
                 current_laser_geometry_verified_for_preview=True, metric_accuracy_accepted=False,
                 diagnostic_axis_max_xyz_rms_mm=max(r["corner_xyz_rms_mm"] for r in axis["residuals"]),
                 axis_validation=report, execution_role=args.execution_role,
                 execution_machine=platform.machine(),
                 geometry_status="experimental_shape_preview_axis_joining_check_failed")
    write(args.output / "diagnostic_geometry.json", model)
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", required=True, type=Path)
    parser.add_argument("--source-images", type=Path)
    parser.add_argument("--scan", type=Path)
    parser.add_argument("--axis-report", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execution-role", choices=["development", "zynq"], default="development")
    args = parser.parse_args()
    if args.execution_role == "zynq" and not platform.machine().lower().startswith(("arm", "aarch")):
        parser.error("Formal current geometry must run on ARM Zynq")
    if not args.axis_report and (not args.source_images or not args.scan):
        parser.error("Camera update requires --source-images and --scan")
    args.output.mkdir(parents=True, exist_ok=False)
    model = compose_diagnostic(args) if args.axis_report else update(args)
    print(json.dumps({"current_laser_geometry_verified_for_preview":
                      model["current_laser_geometry_verified_for_preview"],
                      "diagnostic_preview_only": model.get("diagnostic_preview_only", False),
                      "output": str(args.output)}, indent=2))
    return 0 if model["current_laser_geometry_verified_for_preview"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
