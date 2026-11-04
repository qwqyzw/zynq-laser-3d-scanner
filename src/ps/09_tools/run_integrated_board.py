"""Explicit PYNQ test of the PL+UART release, followed by a bounded 0/5/0 scan.

Default invocation only inspects files. --run-board is required for programming,
camera capture and motor motion. Run with a persistent process/terminal: an
unconfirmed DMA recovery retains buffers and waits for the command 'recover'.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "04_runtime"))
from pl_runtime import inspect_integrated_artifacts, load_integrated_runtime


def save(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def best_effort_save(path, report):
    try:
        save(path, report)
    except BaseException as exc:
        report["report_write_error"] = repr(exc)


def best_effort_print(*parts):
    try:
        print(*parts, flush=True)
    except BaseException:
        pass


def retain_until_quiescent(runtime, path, report):
    """An output/log failure must never release unquiesced physical buffers."""
    if runtime is None:
        return
    driver = runtime.driver
    while not driver.healthy or driver.quarantined_buffer_count:
        report["recovery_hold"] = True
        best_effort_save(path, report)
        best_effort_print("RECOVERY_HOLD: retain process/buffers; send recover")
        try:
            if input().strip() == "recover":
                driver.recover()
        except BaseException:
            best_effort_print(traceback.format_exc())
            try:
                time.sleep(1)
            except BaseException:
                pass
    if report.get("recovery_hold"):
        report["recovery_completed"] = True
        try:
            report["diagnostics_after_recovery"] = driver.diagnostics()
        except BaseException as exc:
            report["diagnostics_after_recovery_error"] = repr(exc)
        best_effort_save(path, report)


def execute(root, *, client_date, roi, exposure):
    from datetime import date
    if date.fromisoformat(client_date).isoformat() != client_date:
        raise ValueError("Supply the client date explicitly")
    root.mkdir(parents=True, exist_ok=False)
    report = {"client_date": client_date, "status": "starting", "roi": roi,
              "exposure": exposure, "geometry_tested": False, "static_cases": [],
              "source_hashes": {}, "cleanup": {}}
    runtime = None
    readonly = None
    try:
        for path in sorted((ROOT / "04_runtime").glob("*.py")):
            report["source_hashes"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        identity = Path("/sys/firmware/devicetree/base/chosen/pynq_board").read_bytes().rstrip(b"\0").decode()
        if identity != "Pynq-Z2":
            raise RuntimeError("Expected the previously verified PYNQ-Z2")
        os.environ["XILINX_XRT"] = "/usr"
        os.environ["BOARD"] = identity
        owners = subprocess.run(["fuser", "/dev/video0"], capture_output=True, text=True, timeout=5)
        if owners.returncode not in (0, 1) or owners.stdout.strip():
            raise RuntimeError("Camera is occupied or ownership cannot be checked")
        from pynq import Overlay, PL, Clocks
        from motor_arduino import ArduinoReadOnlyClient
        # Verify the known stopped base configuration without downloading it.
        # Refuse an unexpected current overlay rather than guessing its ports.
        current = Path(PL.bitfile_name)
        expected = ROOT / "06_fpga_verified/base_recovery/base.bit"
        if hashlib.sha256(current.read_bytes()).digest() != hashlib.sha256(expected.read_bytes()).digest():
            raise RuntimeError("Expected the stopped base recovery configuration before first integrated test")
        old = Overlay(str(current), download=False)
        if not old.is_loaded():
            raise RuntimeError("Base metadata does not match the loaded configuration")
        readonly = ArduinoReadOnlyClient(old, address=1)
        before = []
        for _ in range(3):
            status = readonly.query("status")
            position = readonly.query("position", firmware_family="Emm")
            if not status["position_reached"] or status["stall_detected"] or status["stall_protection_triggered"]:
                raise RuntimeError("Motor is not settled before overlay replacement")
            before.append({"status": status, "position": position})
            time.sleep(.2)
        values = [sample["position"]["motor_position_deg"] for sample in before]
        if max(values) - min(values) > .1:
            raise RuntimeError("Motor position is changing")
        report["before_overlay_readonly"] = {"samples": before, "transactions": readonly.transactions}
        readonly.close()
        readonly = None
        runtime = load_integrated_runtime(timeout=10)
        report["overlay_before"] = {"bitfile": str(PL.bitfile_name), "timestamp": str(PL.timestamp),
                                    "fclk0_mhz": Clocks.fclk0_mhz}
        from motor_uartlite import UARTLiteReadOnlyClient
        readonly = UARTLiteReadOnlyClient(runtime.overlay, address=1)
        received = {name: readonly.query(name, **({"firmware_family": "Emm"} if name == "position" else {}))
                    for name in ("version", "configuration", "status", "voltage", "encoder", "position")}
        if not received["status"]["position_reached"] or received["status"]["stall_detected"] or received["status"]["stall_protection_triggered"]:
            raise RuntimeError("Integrated UART reports a motor fault or movement")
        report["integrated_uart_readonly"] = {"received": received, "transactions": readonly.transactions}
        readonly.close()
        readonly = None
        report["status"] = "UART_passed_testing_static"
        save(root / "completion.json", report)
        print("INTEGRATED_UART_READONLY_PASSED", flush=True)
        # Reuse the frozen validator's comparisons on this actual runtime. Do
        # not call its old overlay loader or change any expected vector.
        sys.path.append(str(ROOT / "06_fpga_verified/software"))
        from verify_board import preflight, case_plan, save_comparison
        from static_image_demo import load_sample
        checks = preflight(ROOT / "06_fpga_verified")
        vectors = root / "static_56"
        vectors.mkdir()
        for ordinal, case in enumerate(case_plan(checks["cases"], 10), 1):
            pixels, config, expected_words = load_sample(ROOT / "06_fpga_verified/handoff", case["sample"], case["mode"])
            start = time.monotonic()
            actual = runtime.driver.run_words(pixels, config, timeout=10)
            row = save_comparison(vectors, ordinal, case, actual, expected_words, time.monotonic() - start)
            report["static_cases"].append({k: v for k, v in row.items() if k != "records"})
            save(root / "completion.json", report)
            if not row["passed"]:
                raise RuntimeError("Integrated overlay failed frozen case %d" % ordinal)
        report["status"] = "static_56_passed_starting_small_scan"
        save(root / "completion.json", report)
        print("INTEGRATED_STATIC_56_PASSED", flush=True)
        from rig_workflow import run_small_scan
        scan = run_small_scan(runtime.overlay, root / "scan", roi, client_session_date=client_date,
                              backend="PL", pl_driver=runtime.driver, verify_ps=True, exposure=exposure)
        report["scan"] = scan
        report["overlay_after"] = {"bitfile": str(PL.bitfile_name), "timestamp": str(PL.timestamp),
                                   "fclk0_mhz": Clocks.fclk0_mhz}
        report["same_overlay_throughout"] = report["overlay_before"] == report["overlay_after"]
        if not report["same_overlay_throughout"]:
            raise RuntimeError("Physical overlay changed during testing")
        if scan["status"] != "completed" or not scan["real_hardware_pipeline_verified"]:
            raise RuntimeError("Integrated small scan did not pass: " + str(scan.get("error", scan["status"])))
        report["status"] = "integrated_static_and_small_scan_passed"
    except BaseException:
        report["status"] = "failed"
        report["traceback"] = traceback.format_exc()
    finally:
        try:
            if readonly is not None:
                try:
                    readonly.close()
                    report["cleanup"]["readonly_closed"] = True
                except BaseException:
                    report["cleanup"]["readonly_close_error"] = traceback.format_exc()
            if runtime is not None:
                report["driver_healthy"] = runtime.driver.healthy
                report["quarantined_buffers"] = runtime.driver.quarantined_buffer_count
                try:
                    report["diagnostics"] = runtime.driver.diagnostics()
                except BaseException as exc:
                    report["diagnostics_error"] = repr(exc)
            best_effort_save(root / "completion.json", report)
            best_effort_print("INTEGRATED_RESULT", report["status"])
        finally:
            # run_scan records RecoveryError. Check ownership even when the
            # error was caught there, and even if writing diagnostics failed.
            retain_until_quiescent(runtime, root / "completion.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-board", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--client-date")
    parser.add_argument("--roi", type=int, nargs=4, default=(175, 340, 400, 400))
    parser.add_argument("--exposure", type=int, default=5)
    args = parser.parse_args()
    if not args.run_board:
        print(json.dumps(inspect_integrated_artifacts(), ensure_ascii=False, indent=2))
        return 0
    if not args.output or not args.client_date:
        parser.error("--run-board requires --output and --client-date")
    result = execute(Path(args.output).resolve(), client_date=args.client_date,
                     roi=tuple(args.roi), exposure=args.exposure)
    return 0 if result["status"] == "integrated_static_and_small_scan_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
