"""Check real returned XYZ and fail-closed handling; no board or GUI access."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "09_tools" / "open_zynq_pointcloud.ps1"
SOURCE = ROOT / "16_zynq_object_scan_20261020" / "board_model" / "viewer"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(directory=None):
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), "-ValidateOnly"]
    if directory is not None:
        args += ["-ResultDirectory", str(directory)]
    return subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    before = {p.relative_to(SOURCE).as_posix(): digest(p) for p in SOURCE.rglob("*") if p.is_file()}
    checks = []

    def check(name, value, detail=""):
        checks.append({"name": name, "passed": bool(value), "detail": detail})
        if not value:
            raise AssertionError(f"{name}: {detail}")

    real = run()
    check("default_real_export_accepted", real.returncode == 0, real.stderr)
    receipt = json.loads(real.stdout)
    check("real_count_and_producer", receipt["points"] == 1267 and receipt["coordinate_producer"] == "zynq")
    check("diagnostic_flags_preserved", receipt["diagnostic_preview_only"] is True and receipt["alignment_validated"] is False and receipt["metric_accuracy_accepted"] is False)
    check("no_pc_xyz_or_gui", receipt["pc_coordinate_computation"] is False and receipt["pc_data_modified"] is False and receipt["gui_launched"] is False)
    check("default_is_current_16_result", Path(receipt["result_directory"]).resolve() == SOURCE.resolve())

    with tempfile.TemporaryDirectory(prefix="zynq returned scan '") as temporary:
        model_root = Path(temporary) / "中文 空格 & (model)"
        current = model_root / "viewer"

        def reset():
            if current.exists():
                shutil.rmtree(current)
            shutil.copytree(SOURCE, current)
            return read_json(current / "export_manifest.json")

        def store(manifest):
            write_json(current / "export_manifest.json", manifest)

        def bind(manifest, name):
            path = current / name
            manifest["outputs"][name] = {"sha256": digest(path), "bytes": path.stat().st_size}
            store(manifest)

        def reject(name):
            result = run(current)
            check(name, result.returncode != 0 and "Point-cloud display rejected" in result.stderr, result.stderr)

        reset()
        safe = run(model_root)
        check("model_root_and_unicode_space_quote_path", safe.returncode == 0, safe.stderr)

        manifest = reset()
        manifest["test_only"] = True
        store(manifest)
        reject("test_export_rejected")

        manifest = reset()
        manifest["coordinate_producer"] = "pc"
        store(manifest)
        reject("pc_producer_rejected")

        manifest = reset()
        manifest["data_origin"] = "synthetic"
        store(manifest)
        reject("synthetic_origin_rejected")

        manifest = reset()
        manifest["synthetic_points_added"] = 1
        store(manifest)
        reject("added_points_rejected")

        reset()
        ply = current / "points.ply"
        ply.write_bytes(ply.read_bytes() + b"0 0 0\n")
        reject("altered_bytes_rejected")

        manifest = reset()
        ply = current / "points.ply"
        ply.write_text(ply.read_text(encoding="ascii").replace("element vertex 1267", "element vertex 1266"), encoding="ascii")
        bind(manifest, "points.ply")
        reject("self_consistent_hash_wrong_header_count_rejected")

        manifest = reset()
        ply = current / "points.ply"
        lines = ply.read_text(encoding="ascii").splitlines()
        index = lines.index("end_header") + 1
        lines[index] = "NaN 0 0"
        ply.write_text("\n".join(lines) + "\n", encoding="ascii")
        bind(manifest, "points.ply")
        reject("self_consistent_hash_nonfinite_xyz_rejected")

        manifest = reset()
        meta_path = current / "viewer_meta.json"
        metadata = read_json(meta_path)
        metadata["alignment_validated"] = True
        write_json(meta_path, metadata)
        bind(manifest, "viewer_meta.json")
        reject("hidden_failed_alignment_rejected")

        manifest = reset()
        manifest["outputs"]["../outside.txt"] = {"sha256": "0" * 64, "bytes": 0}
        store(manifest)
        reject("escaping_output_path_rejected")

        reset()
        (current / "source_meta.json").unlink()
        reject("missing_diagnostic_metadata_rejected")

        manifest = reset()
        manifest["axis_units"]["z"] = "m"
        store(manifest)
        reject("inconsistent_axis_units_rejected")

    after = {p.relative_to(SOURCE).as_posix(): digest(p) for p in SOURCE.rglob("*") if p.is_file()}
    check("original_board_return_bytes_unchanged", before == after)
    result = {"status": "cloudcompare_handoff_checks_passed", "checks_passed": len(checks), "checks": checks, "real_validation_receipt": receipt, "hardware_access": False, "gui_started": False}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
