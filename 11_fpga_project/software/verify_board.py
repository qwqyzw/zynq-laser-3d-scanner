"""Validate this transfer bundle; explicitly opt in to PYNQ-Z2 board tests.

Default: python3 software/verify_board.py --check-only
Board:   python3 software/verify_board.py --run-board

If DMA recovery fails, a recovery hold keeps physical buffers alive until the
driver confirms recovery or the board is powered off. See the bundle guide.
No PYNQ package, Overlay, MMIO, or hardware is imported in check-only mode.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import sys
import time
import traceback
import zipfile

import numpy as np

from laser_stripe import LaserStripe, RecoveryError, decode_words
from static_image_demo import load_sample


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# In the complete project, use the frozen same-root runtime release by default.
if (PROJECT_ROOT.parent / "06_fpga_verified").is_dir():
    PROJECT_ROOT = PROJECT_ROOT.parent / "06_fpga_verified"
TOLERANCE_PX = 0.25
_RETAINED_RUNTIME = None


class ValidationFailure(RuntimeError):
    pass


class Tee:
    def __init__(self, terminal, log):
        self.terminal, self.log = terminal, log

    def write(self, text):
        self.terminal.write(text)
        self.log.write(text)
        self.log.flush()
        return len(text)

    def flush(self):
        self.terminal.flush()
        self.log.flush()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def environment_info():
    """Inspect host facts without importing PYNQ or touching hardware registers."""
    info = {"python": sys.version, "executable": sys.executable,
            "platform": platform.platform(), "system": platform.system(),
            "machine": platform.machine(), "numpy_version": np.__version__,
            "board_environment": os.environ.get("BOARD", ""),
            "python_interactive": bool(sys.flags.interactive),
            "pynq_version": None, "device_tree_model": None}
    try:
        info["pynq_version"] = importlib.metadata.version("pynq")
    except importlib.metadata.PackageNotFoundError:
        pass
    for path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            info["device_tree_model"] = Path(path).read_bytes().rstrip(b"\x00").decode("utf-8", "replace")
            break
        except OSError:
            continue
    return info


def verify_hashes(root):
    manifest = json.loads((root / "SHA256.json").read_text(encoding="utf-8-sig"))
    entries = manifest["files"]
    if not isinstance(entries, list) or not entries:
        raise ValidationFailure("SHA256.json has no file entries")
    seen = set()
    for entry in entries:
        rel = entry["path"]
        if not isinstance(rel, str):
            raise ValidationFailure("Hash paths must be strings")
        part = PurePosixPath(rel)
        if ("\\" in rel or part.is_absolute()
                or ".." in part.parts or ":" in rel or rel in seen):
            raise ValidationFailure("Unsafe or duplicate hash entry: " + repr(rel))
        seen.add(rel)
        path = (root / rel).resolve()
        if root.resolve() not in path.parents:
            raise ValidationFailure("Hash entry escapes package: " + rel)
        if not path.is_file():
            raise ValidationFailure("Missing package file: " + rel)
        if path.stat().st_size != entry["bytes"]:
            raise ValidationFailure("File length mismatch: " + rel)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != str(entry["sha256"]).lower():
            raise ValidationFailure("SHA256 mismatch: " + rel)
    present = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts[0] == "results" or "__pycache__" in rel.parts or rel.as_posix() == "SHA256.json":
            continue
        present.add(rel.as_posix())
    extras = present - seen
    if extras:
        raise ValidationFailure("Unlisted package files: " + ", ".join(sorted(extras)))
    return seen


def preflight(root):
    listed = verify_hashes(root)
    manifest = json.loads((root / "handoff/manifest.json").read_text(encoding="utf-8"))
    samples = manifest["samples"]
    names = [sample["name"] for sample in samples]
    if len(names) != 23 or len(set(names)) != 23:
        raise ValidationFailure("Expected exactly 23 unique manifest samples")
    required = {"laser_static.bit", "laser_static.hwh", "software/verify_board.py",
                "software/laser_stripe.py", "software/static_image_demo.py", "handoff/manifest.json"}
    cases = []
    for name in names:
        if Path(name).name != name or Path(name).suffix.lower() != ".png":
            raise ValidationFailure("Unexpected sample name: " + repr(name))
        stem = Path(name).stem
        required.add("handoff/vectors/" + stem + "_input.bin")
        for mode in ("core", "full"):
            required.add("handoff/expected/" + stem + "_" + mode + ".hex")
            _, config, expected = load_sample(root / "handoff", name, mode)
            valid, _ = decode_words(expected)
            declared = next(s for s in samples if s["name"] == name).get("valid_columns", {}).get(mode)
            if declared is not None and int(valid.sum()) != declared:
                raise ValidationFailure("Expected-valid count differs from manifest: " + name + "/" + mode)
            cases.append({"sample": name, "mode": mode, "config": config.__dict__,
                          "expected_valid_columns": int(valid.sum())})
    missing = required - listed
    if missing:
        raise ValidationFailure("Required files are absent from SHA256.json: " + ", ".join(sorted(missing)))
    if "flat_zero.png" not in names or "flat_laser_off.png" not in names:
        raise ValidationFailure("Baseline positive/negative samples are missing")
    for name, positive in (("flat_zero.png", True), ("flat_laser_off.png", False)):
        selected = [case for case in cases if case["sample"] == name]
        if any((case["expected_valid_columns"] > 0) != positive for case in selected):
            raise ValidationFailure("Baseline sample has unexpected valid-point count: " + name)
    return {"files_hashed": len(listed), "input_samples": 23, "expected_cases": 46,
            "cases": cases}


def assert_board_environment(info):
    if info["system"] != "Linux" or not info["machine"].lower().startswith("arm"):
        raise ValidationFailure("Board mode requires Linux on 32-bit ARM (PYNQ-Z2); use --check-only on a PC")
    identities = [info.get("board_environment") or "", info.get("device_tree_model") or ""]
    if not any("pynqz2" in "".join(c for c in value.lower() if c.isalnum()) for value in identities):
        raise ValidationFailure("Cannot identify PYNQ-Z2 from BOARD or device-tree model. Confirm the physical board; "
                                "only for a confirmed PYNQ-Z2, export BOARD=Pynq-Z2 and retry")


@dataclass
class BoardRuntime:
    overlay: object
    driver: object


def load_board(root, timeout):
    # This is the ONLY overlay download in this program. The adapter binds
    # MMIO directly; never construct PYNQ's native DMA channel objects.
    from pynq import Overlay
    overlay = Overlay(str(root / "laser_static.bit"))
    return BoardRuntime(overlay, LaserStripe.from_overlay(
        overlay, ip_name="laser_stripe_0", dma_name="axi_dma_0", timeout=timeout))


def case_plan(cases, repeat_frames):
    baseline = [case for case in cases if case["sample"] == "flat_zero.png"]
    remainder = [case for case in cases if case["sample"] != "flat_zero.png"]
    plan = [dict(case, group="baseline") for case in baseline]
    plan.extend(dict(case, group="frozen_dataset") for case in remainder)
    for index in range(repeat_frames):
        name = ("flat_zero.png", "flat_laser_off.png")[index % 2]
        case = next(c for c in cases if c["sample"] == name and c["mode"] == "full")
        plan.append(dict(case, group="positive_negative_repeat", repeat_frame=index + 1))
    return plan


def save_comparison(directory, ordinal, case, result, expected, elapsed):
    prefix = directory / (f"{ordinal:03d}_" + Path(case["sample"]).stem + "_" + case["mode"])
    actual = result.words
    # Reject implicit dtype conversions: malformed output must fail visibly.
    if not isinstance(actual, np.ndarray) or actual.dtype != np.dtype(np.uint32) or actual.shape != (640,):
        raise ValidationFailure("Driver did not return exactly 640 uint32 output words")
    np.savetxt(prefix.with_suffix(".hex"), actual, fmt="%08X")
    expected_valid, expected_y = decode_words(expected)
    format_error = None
    try:
        valid, y = decode_words(actual)
    except Exception as exc:
        format_error = str(exc)
        valid = (actual & np.uint32(0x80000000)) != 0
        y = np.full(640, np.nan)
    common = valid & expected_valid
    maximum = (float(np.max(np.abs(y[common] - expected_y[common])))
               if np.any(common) and not format_error else 0.0)
    mismatches = int(np.count_nonzero(valid != expected_valid))
    passed = not format_error and mismatches == 0 and maximum <= TOLERANCE_PX
    rows = []
    for x in range(640):
        actual_y = float(y[x]) if np.isfinite(y[x]) else None
        wanted_y = float(expected_y[x]) if expected_valid[x] else None
        error = abs(actual_y - wanted_y) if actual_y is not None and wanted_y is not None else None
        valid_match = bool(valid[x] == expected_valid[x])
        rows.append({"x": x, "actual_hex": f"{int(actual[x]):08X}",
                     "expected_hex": f"{int(expected[x]):08X}",
                     "actual_valid": bool(valid[x]), "expected_valid": bool(expected_valid[x]),
                     "actual_y_px": actual_y, "expected_y_px": wanted_y,
                     "absolute_error_px": error, "valid_match": valid_match,
                     "passed": bool(not format_error and valid_match and (error is None or error <= TOLERANCE_PX))})
    report = {**case, "ordinal": ordinal, "passed": passed,
              "actual_valid_columns": int(valid.sum()), "valid_mismatches": mismatches,
              "max_coordinate_difference_px": maximum if not format_error else None,
              "tolerance_px": TOLERANCE_PX, "format_error": format_error,
              "cycles": int(result.cycles), "completed_frames": int(result.completed_frames),
              "wall_time_seconds": elapsed, "records": rows}
    write_json(prefix.with_suffix(".json"), report)
    with prefix.with_suffix(".csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {key: value for key, value in report.items() if key != "records"}


def _hold_notice(message):
    try:
        print(message, flush=True)
    except BaseException:
        # A lost terminal must never tear down owners of unsafe buffers.
        pass


def recovery_hold(runtime):
    """An actual failed reset requires preserving the physical-buffer owner."""
    _hold_notice("\nRECOVERY HOLD: Python retains buffers that DMA may still access.\n"
          "Keep this terminal/Python running. Download the RETURN zip through another Jupyter/SCP session.\n"
          "Type recover for one bounded recovery attempt. Tests will NOT resume.\n"
          "If recovery keeps failing, shut down/power-cycle the BOARD. Do not kill Python alone.\n"
          "Recovery hold [type recover and Enter]:")
    last_notice = 0.0
    while True:
        try:
            command = input().strip().lower()
            if command != "recover":
                _hold_notice("Only recover is accepted while physical buffers are unsafe; keep this process alive.")
                continue
            try:
                runtime.driver.recover()
            except BaseException as exc:
                _hold_notice("Recovery still failed: " + repr(exc))
                continue
            _hold_notice("Core and both DMA channels are confirmed idle; buffers released. Test remains FAILED.")
            return
        except BaseException:
            if time.monotonic() - last_notice >= 30:
                _hold_notice("Recovery hold remains active. Keep Python alive; power-cycle the board if recovery is unavailable.")
                last_notice = time.monotonic()
            # A detached terminal must not spin or release buffer owners.
            try:
                time.sleep(1.0)
            except BaseException:
                pass


def archive_results(root, directory, relative_zip):
    with zipfile.ZipFile(root / relative_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, str(path.relative_to(directory.parent)))


def _run_validation(root=PROJECT_ROOT, *, run_board=False, timeout=10.0, repeat_frames=10,
                   runtime_factory=None, info_provider=environment_info, offline_simulation=False,
                   hold_handler=None):
    """Workflow with explicit, always-labelled offline injection for unit tests."""
    global _RETAINED_RUNTIME
    if (runtime_factory is not None or hold_handler is not None) and not offline_simulation:
        raise ValueError("Injected runtimes require offline_simulation=True; they are not board evidence")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive and finite")
    if isinstance(repeat_frames, bool) or not isinstance(repeat_frames, int) or repeat_frames < 10:
        raise ValueError("at least 10 alternating positive/negative frames are required")
    root = Path(root).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    directory = root / "results" / ("board_verify_" + stamp)
    directory.mkdir(parents=True)
    summary = {"format_version": 1, "started_utc": stamp, "mode": "run_board" if run_board else "check_only",
               "evidence": "offline_simulation" if offline_simulation else "not_hardware_test",
               "hardware_exercised": False, "board_validation_passed": False,
               "status": "running", "repeat_frames": repeat_frames,
               "planned_board_cases": 46 + repeat_frames if run_board else 0,
               "completed_cases": [], "requires_board_reboot_before_python_exit": False}
    runtime = None
    start = time.monotonic()
    with (directory / "console.log").open("w", encoding="utf-8") as log:
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            try:
                print("Mode:", "OFFLINE SIMULATION" if offline_simulation else summary["mode"], flush=True)
                info = info_provider()
                write_json(directory / "environment.json", info)
                checks = preflight(root)
                summary["preflight"] = checks
                print(f"Package checks passed: {checks['files_hashed']} hashes, 23 inputs, 46 configurations", flush=True)
                if not run_board:
                    summary["status"] = "check_only_passed"
                    print("CHECK ONLY PASSED. No hardware was loaded or tested.", flush=True)
                else:
                    if not offline_simulation:
                        assert_board_environment(info)
                    plan = case_plan(checks["cases"], repeat_frames)
                    factory = runtime_factory or load_board
                    print("Loading laser_static.bit once; binding laser_stripe_0 / axi_dma_0", flush=True)
                    runtime = factory(root, timeout)
                    summary["evidence"] = "offline_simulation" if offline_simulation else "real_board_execution"
                    for ordinal, case in enumerate(plan, 1):
                        summary["current_case"] = {**case, "ordinal": ordinal}
                        write_json(directory / "summary.json", summary)
                        pixels, config, expected = load_sample(root / "handoff", case["sample"], case["mode"])
                        print(f"[{ordinal}/{len(plan)}] {case['sample']} {case['mode']} {case['group']}", flush=True)
                        before = time.monotonic()
                        if not offline_simulation:
                            summary["hardware_exercised"] = True
                        result = runtime.driver.run_words(pixels, config, timeout=timeout)
                        labelled_case = dict(case, evidence=summary["evidence"],
                                             is_board_measurement=not offline_simulation)
                        report = save_comparison(directory, ordinal, labelled_case, result, expected, time.monotonic() - before)
                        summary["completed_cases"].append(report)
                        write_json(directory / "summary.json", summary)
                        print(f"  {'PASS' if report['passed'] else 'FAIL'}: valid={report['actual_valid_columns']}, "
                              f"mismatches={report['valid_mismatches']}, max_delta_px={report['max_coordinate_difference_px']}", flush=True)
                        if not report["passed"]:
                            raise ValidationFailure("Output comparison failed; stopping immediately")
                    summary["current_case"] = None
                    summary["status"] = "offline_simulation_passed" if offline_simulation else "board_passed"
                    summary["board_validation_passed"] = not offline_simulation
                    print(f"All {len(plan)} cases passed on the same loaded overlay and driver.", flush=True)
            except BaseException as exc:
                if isinstance(exc, (SystemExit, GeneratorExit)):
                    raise
                summary["board_validation_passed"] = False
                if isinstance(exc, RecoveryError) and runtime is not None:
                    if getattr(runtime.driver, "quarantined_buffer_count", 0):
                        # Retain before any diagnostic write that could fail
                        # (disk full, detached terminal, interrupted archival).
                        _RETAINED_RUNTIME = runtime
                summary["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
                summary["error"] = {"type": type(exc).__name__, "message": str(exc),
                                    "diagnostics": getattr(exc, "diagnostics", {})}
                trace = traceback.format_exc()
                summary["traceback"] = trace
                (directory / "traceback.txt").write_text(trace, encoding="utf-8")
                print(trace, file=sys.stderr, flush=True)
                if isinstance(exc, RecoveryError):
                    # Keep both the overlay and driver alive; never free physical
                    # buffers, reload base, or rearm DMA after failed recovery.
                    count = getattr(getattr(runtime, "driver", None), "quarantined_buffer_count", 0)
                    summary["quarantined_buffers"] = count
                    summary["requires_board_reboot_before_python_exit"] = bool(count)
                    print("RECOVERY FAILED: stop all tests. Do not free buffers or terminate Python while "
                          "quarantined buffers are reported. A recovery hold will preserve this process. "
                          "Download the RETURN zip from another session. If bounded recovery cannot work, "
                          "shut down/power-cycle the board; do not kill Python alone.", file=sys.stderr, flush=True)
            finally:
                summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
                summary["wall_time_seconds"] = time.monotonic() - start
                summary["completed_case_count"] = len(summary["completed_cases"])
                summary["return_zip"] = "results/RETURN_" + directory.name + ".zip"
                write_json(directory / "summary.json", summary)
                print("Result:", summary["status"], flush=True)
                print("Return this zip:", root / summary["return_zip"], flush=True)
    # Console is closed before archiving so even the final log lines are saved.
    archive_results(root, directory, summary["return_zip"])
    return summary


def run_validation(root=PROJECT_ROOT, *, run_board=False, timeout=10.0, repeat_frames=10,
                   runtime_factory=None, info_provider=environment_info, offline_simulation=False,
                   hold_handler=None):
    """Save results first; always retain unsafe DMA owners even if saving fails."""
    global _RETAINED_RUNTIME
    summary = None
    held = False
    try:
        summary = _run_validation(root, run_board=run_board, timeout=timeout, repeat_frames=repeat_frames,
                                  runtime_factory=runtime_factory, info_provider=info_provider,
                                  offline_simulation=offline_simulation, hold_handler=hold_handler)
    finally:
        if _RETAINED_RUNTIME is not None:
            if offline_simulation:
                if hold_handler is not None:
                    hold_handler(_RETAINED_RUNTIME)
            else:
                recovery_hold(_RETAINED_RUNTIME)
                _RETAINED_RUNTIME = None
                held = True
    if held and summary is not None:
        summary["requires_board_reboot_before_python_exit"] = False
        summary["recovery_hold_released_utc"] = datetime.now(timezone.utc).isoformat()
        root = Path(root).resolve()
        directory = root / "results" / Path(summary["return_zip"]).stem[len("RETURN_"):]
        write_json(directory / "summary.json", summary)
        archive_results(root, directory, summary["return_zip"])
    return summary


def positive_timeout(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive and finite")
    return value


def repeat_count(value):
    value = int(value)
    if value < 10:
        raise argparse.ArgumentTypeError("repeats must be at least 10 alternating positive/negative frames")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true", help="default: hashes/data only, no PYNQ import")
    mode.add_argument("--run-board", action="store_true", help="explicitly load bitstream and test DMA")
    parser.add_argument("--timeout", type=positive_timeout, default=10.0, help="seconds per frame (default 10)")
    parser.add_argument("--repeats", type=repeat_count, default=10, help="alternating positive/negative frames (default 10)")
    args = parser.parse_args(argv)
    return run_validation(run_board=args.run_board, timeout=args.timeout, repeat_frames=args.repeats)


if __name__ == "__main__":
    LAST_SUMMARY = main()
    if not sys.flags.interactive:
        raise SystemExit(0 if LAST_SUMMARY["status"] in ("check_only_passed", "board_passed") else 1)
