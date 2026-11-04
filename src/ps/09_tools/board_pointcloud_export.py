"""Export already reconstructed board XYZ points; no geometry is computed here.

Real-data exports require explicit hardware/Zynq provenance and execution on a
Linux ARM Zynq/PYNQ board. --allow-test-data makes a visibly labelled test export
and never produces a real-data claim. This module uses only the Python stdlib.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from render_object_preview import finite_points, render


MARKERS = ("synthetic", "合成数据", "synthetic_tool_check_only")
OUTPUT_NAMES = ("points.csv", "source_meta.json", "viewer_meta.json", "points.ply",
                "preview.html", "export_manifest.json")


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def contains_marker(value):
    if isinstance(value, str):
        lowered = value.casefold()
        return any(marker in lowered for marker in MARKERS)
    if isinstance(value, list):
        return any(contains_marker(item) for item in value)
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in ("synthetic", "test_only", "is_synthetic"):
                if item not in (False, None, 0, "false"):
                    return True
            if contains_marker(item):
                return True
    return False


def csv_contains_marker(path: Path):
    # Include non-coordinate text columns; retain overlap for a split marker.
    overlap = ""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                return False
            text = overlap + chunk
            if contains_marker(text):
                return True
            overlap = text[-64:]


def board_identity():
    identity = {"system": platform.system(), "machine": platform.machine(),
                "hostname": socket.gethostname(), "python": platform.python_version()}
    for name, filename in (
        ("device_tree_model", "/sys/firmware/devicetree/base/model"),
        ("device_tree_compatible", "/sys/firmware/devicetree/base/compatible"),
        ("pynq_board", "/sys/firmware/devicetree/base/chosen/pynq_board"),
    ):
        try:
            identity[name] = Path(filename).read_bytes().decode("utf-8", "replace").replace("\x00", " ").strip()
        except OSError:
            identity[name] = None
    arm = identity["machine"].casefold().startswith(("arm", "aarch64"))
    descriptions = " ".join(str(identity.get(key) or "") for key in
                            ("device_tree_model", "device_tree_compatible", "pynq_board")).casefold()
    identity["is_linux_arm_zynq_or_pynq"] = (
        identity["system"] == "Linux" and arm and ("zynq" in descriptions or "pynq" in descriptions))
    return identity


def validate_metadata(metadata, *, allow_test_data=False):
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    units = metadata.get("units")
    if not isinstance(units, str) or not units.strip():
        raise ValueError("metadata.units must specify the XYZ coordinate unit")
    units = units.strip()
    axis_units = metadata.get("axis_units", {axis: units for axis in "xyz"})
    if not isinstance(axis_units, dict) or any(axis not in axis_units for axis in "xyz"):
        raise ValueError("metadata.axis_units must specify x, y, and z")
    if any(not isinstance(axis_units[axis], str) or axis_units[axis].strip() != units
           for axis in "xyz"):
        raise ValueError("all three axis units must equal metadata.units for an equal-scale viewer")
    if not allow_test_data:
        if metadata.get("data_origin") != "hardware_scan":
            raise ValueError("real export requires metadata.data_origin='hardware_scan'")
        if metadata.get("coordinate_producer") != "zynq":
            raise ValueError("real export requires metadata.coordinate_producer='zynq'")
        if contains_marker(metadata):
            raise ValueError("synthetic/test metadata cannot be exported as real scan data")
    return units, {axis: units for axis in "xyz"}


def copy_source_images(metadata, meta_dir, output_dir):
    """Copy existing local evidence bytes; do not resize, process, or fetch them."""
    links, copied, seen = [], [], {}
    items = metadata.get("source_images", [])
    if not isinstance(items, list):
        raise ValueError("metadata.source_images must be a list when present")
    for index, item in enumerate(items):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        value = item["path"]
        drive = len(value) >= 3 and value[1] == ":" and value[2] in "\\/"
        if not drive and urlsplit(value).scheme:
            continue
        source = Path(value)
        if not source.is_absolute():
            source = (meta_dir / source).resolve()
        if not source.is_file():
            continue
        key = str(source.resolve())
        if key not in seen:
            destination = output_dir / "source_images" / (f"{index:03d}_" + source.name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            relative = destination.relative_to(output_dir).as_posix()
            seen[key] = relative
            copied.append({"source_path": str(source), "output_path": relative,
                           "sha256": sha256(destination), "bytes": destination.stat().st_size})
        links.append({"path": seen[key], "label": str(item.get("label", f"原始照片 {index + 1}"))})
    return links, copied


def export(csv_path: Path, meta_path: Path, output_dir: Path, *, allow_test_data=False,
           max_points=24000, columns=None):
    csv_path, meta_path, output_dir = csv_path.resolve(), meta_path.resolve(), output_dir.resolve()
    input_hashes = {"csv": sha256(csv_path), "metadata": sha256(meta_path)}
    metadata = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    units, axis_units = validate_metadata(metadata, allow_test_data=allow_test_data)
    identity = board_identity()
    if not allow_test_data:
        if (contains_marker(str(csv_path)) or contains_marker(str(meta_path))
                or contains_marker(str(output_dir)) or csv_contains_marker(csv_path)):
            raise ValueError("SYNTHETIC marker cannot be exported as a real scan result")
        if not identity["is_linux_arm_zynq_or_pynq"]:
            raise ValueError("real scan export must execute on a Linux ARM Zynq/PYNQ board")
    points, point_stats = finite_points(csv_path, columns)
    if sha256(csv_path) != input_hashes["csv"] or sha256(meta_path) != input_hashes["metadata"]:
        raise RuntimeError("input source changed while reading coordinates")
    # finite_points rejects an empty/entirely invalid cloud. XYZ are passed
    # through unchanged, and the PLY has no face list or invented vertices.
    if any((output_dir / name).exists() for name in OUTPUT_NAMES):
        raise FileExistsError("export output files already exist; choose a new evidence directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    copied_csv, copied_meta = output_dir / "points.csv", output_dir / "source_meta.json"
    shutil.copyfile(csv_path, copied_csv)
    shutil.copyfile(meta_path, copied_meta)
    if sha256(copied_csv) != input_hashes["csv"] or sha256(copied_meta) != input_hashes["metadata"]:
        raise RuntimeError("input source changed during evidence copying")
    view_meta = dict(metadata)
    view_meta["units"], view_meta["axis_units"] = units, axis_units
    view_meta["source_images"], copied_images = copy_source_images(metadata, meta_path.parent, output_dir)
    view_meta["export_test_only"] = bool(allow_test_data)
    if allow_test_data:
        view_meta["title"] = "仅供导出工具检查 · 合成数据"
        view_meta["geometry_status"] = "工具 IO 自测，绝非实物扫描结果"
        view_meta["coverage_note"] = "此点云仅验证文件导出，不构成硬件扫描或几何验证。"
    viewer_meta_path = output_dir / "viewer_meta.json"
    viewer_meta_path.write_text(json.dumps(view_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ply_path = output_dir / "points.ply"
    with ply_path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write("ply\nformat ascii 1.0\n")
        handle.write("comment existing XYZ only; no fitting, faces, or added vertices\n")
        handle.write("comment test_only " + str(bool(allow_test_data)).lower() + "\n")
        handle.write("comment xyz_unit_utf8_hex " + units.encode("utf-8").hex() + "\n")
        handle.write(f"element vertex {len(points)}\nproperty double x\nproperty double y\nproperty double z\nend_header\n")
        for point in points:
            handle.write(" ".join(format(value, ".17g") for value in point) + "\n")
    preview_stats = render(copied_csv, viewer_meta_path, output_dir / "preview.html",
                           columns=columns, max_points=max_points)
    outputs = {}
    for name in OUTPUT_NAMES:
        if name == "export_manifest.json":
            continue
        path = output_dir / name
        outputs[name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    for item in copied_images:
        outputs[item["output_path"]] = {"sha256": item["sha256"], "bytes": item["bytes"]}
    # Check the source once more so a concurrent capture cannot silently change
    # a point CSV while its original data and converted PLY are being recorded.
    if sha256(csv_path) != input_hashes["csv"] or sha256(meta_path) != input_hashes["metadata"]:
        raise RuntimeError("input source changed while exporting")
    report = {
        "schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "timestamp_note": "Host/board wall-clock time; authenticity does not rely on clock correctness.",
        "real_scan_export": not allow_test_data, "test_only": bool(allow_test_data),
        "execution_platform": identity, "data_origin": metadata.get("data_origin"),
        "coordinate_producer": metadata.get("coordinate_producer"),
        "coordinate_computation_performed": False, "geometry_fitting_performed": False,
        "surface_fitted": False, "synthetic_points_added": 0,
        "units": units, "axis_units": axis_units,
        "input_sources": {"csv": {"path": str(csv_path), "sha256": input_hashes["csv"]},
                          "metadata": {"path": str(meta_path), "sha256": input_hashes["metadata"]}},
        "source_scripts": {Path(__file__).name: sha256(Path(__file__)),
                           "render_object_preview.py": sha256(Path(__file__).with_name("render_object_preview.py"))},
        "point_statistics": point_stats, "preview_statistics": preview_stats,
        "copied_source_images": copied_images, "outputs": outputs,
    }
    (output_dir / "export_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("meta", type=Path)
    parser.add_argument("outputdir", type=Path)
    parser.add_argument("--max-points", type=int, default=24000)
    parser.add_argument("--allow-test-data", action="store_true",
                        help="IO checks only: permanently mark all exported artifacts test-only")
    parser.add_argument("--x-column")
    parser.add_argument("--y-column")
    parser.add_argument("--z-column")
    args = parser.parse_args()
    columns = (args.x_column, args.y_column, args.z_column)
    if any(columns) and not all(columns):
        parser.error("specify all three coordinate columns together")
    try:
        report = export(args.csv, args.meta, args.outputdir,
                        allow_test_data=args.allow_test_data, max_points=args.max_points,
                        columns=columns if all(columns) else None)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Export refused: {exc}\n")
    print(json.dumps({"real_scan_export": report["real_scan_export"],
                      "finite_points": report["point_statistics"]["finite_points"],
                      "output_directory": str(args.outputdir.resolve()),
                      "coordinate_computation_performed": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
