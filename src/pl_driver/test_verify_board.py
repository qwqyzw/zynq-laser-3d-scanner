"""Offline runner tests. Fake results are always labelled offline_simulation."""
import builtins
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

from laser_stripe import RecoveryError, StripeResult, decode_words
from static_image_demo import load_sample
import verify_board as runner


class FakeDriver:
    def __init__(self, root, fault_at=None, fault="mismatch"):
        self.root, self.fault_at, self.fault = root, fault_at, fault
        self.calls = []
        self.quarantined_buffer_count = 0

    def run_words(self, words, config, timeout):
        self.calls.append((config.x0, config.min_run, timeout))
        number = len(self.calls)
        if self.fault_at == number and self.fault == "recovery":
            self.quarantined_buffer_count = 2
            raise RecoveryError("OFFLINE simulated unquiesced DMA", {"offline": True})
        if self.fault_at == number and self.fault == "interrupt":
            raise KeyboardInterrupt()
        name = self.root_names[config.x0]
        _, _, output = load_sample(self.root / "handoff", name, "core" if config.min_run == 0 else "full")
        output = output.copy()
        if self.fault_at == number:
            if self.fault == "format":
                output[0] = 1
            else:
                output[10] = 0 if output[10] else 0x80000000 | (200 * 256)
        valid = (output & np.uint32(0x80000000)) != 0
        y = (output & np.uint32(0x3FFFF)).astype(float) / 256
        return StripeResult(output, valid, y, 10000, number)


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.names = ["flat_zero.png", "flat_laser_off.png"] + [f"sample_{i:02d}.png" for i in range(2, 23)]
        for directory in ("software", "handoff/vectors", "handoff/expected"):
            (cls.root / directory).mkdir(parents=True)
        for name in ("laser_static.bit", "laser_static.hwh", "software/verify_board.py",
                     "software/laser_stripe.py", "software/static_image_demo.py"):
            (cls.root / name).write_text("OFFLINE FIXTURE ONLY\n")
        pixels = np.zeros(640 * 480, dtype="<u4").tobytes()
        samples = []
        for index, name in enumerate(cls.names):
            (cls.root / "handoff/vectors" / (Path(name).stem + "_input.bin")).write_bytes(pixels)
            words = np.zeros(640, np.uint32)
            if index != 1:
                words[10] = 0x80000000 | (200 * 256 + 128)
            for mode in ("core", "full"):
                np.savetxt(cls.root / "handoff/expected" / (Path(name).stem + "_" + mode + ".hex"), words, fmt="%08X")
            samples.append({"name": name, "roi": [index, 0, 640, 480], "threshold": 12.0,
                            "valid_columns": {"core": int(index != 1), "full": int(index != 1)}})
        (cls.root / "handoff/manifest.json").write_text(json.dumps({"samples": samples}))
        files = [{"path": p.relative_to(cls.root).as_posix(), "bytes": p.stat().st_size,
                  "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in cls.root.rglob("*") if p.is_file()]
        (cls.root / "SHA256.json").write_text(json.dumps({"files": files}))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        runner._RETAINED_RUNTIME = None

    def tearDown(self):
        runner._RETAINED_RUNTIME = None

    def execute(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return runner.run_validation(self.root, **kwargs)

    def fake_factory(self, fault_at=None, fault="mismatch"):
        calls = []
        driver = FakeDriver(self.root, fault_at, fault)
        driver.root_names = self.names
        def factory(root, timeout):
            calls.append((root, timeout))
            return runner.BoardRuntime(object(), driver)
        return factory, calls, driver

    def test_check_only_never_imports_pynq_and_writes_return_zip(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name == "pynq" or name.startswith("pynq."):
                raise AssertionError("Check-only imported PYNQ")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded), patch.object(runner, "load_board") as board:
            result = self.execute()
        board.assert_not_called()
        self.assertEqual(result["status"], "check_only_passed")
        self.assertEqual(result["preflight"]["expected_cases"], 46)
        self.assertFalse(result["hardware_exercised"])
        self.assertFalse(result["board_validation_passed"])
        with zipfile.ZipFile(self.root / result["return_zip"]) as archive:
            self.assertTrue(any(n.endswith("console.log") for n in archive.namelist()))
            saved = json.loads(archive.read(next(n for n in archive.namelist() if n.endswith("summary.json"))))
        self.assertEqual(saved["evidence"], "not_hardware_test")

    def test_hash_failure_gates_any_board_load_and_preserves_failure(self):
        path = self.root / "laser_static.bit"
        before = path.read_bytes()
        try:
            path.write_bytes(before[:-1] + b"!")
            with patch.object(runner, "load_board") as board:
                result = self.execute(run_board=True)
            board.assert_not_called()
            self.assertEqual(result["status"], "failed")
            self.assertIn("SHA256 mismatch", result["error"]["message"])
            self.assertEqual(result["completed_case_count"], 0)
            self.assertTrue((self.root / result["return_zip"]).is_file())
        finally:
            path.write_bytes(before)

    def test_unlisted_python_file_gates_load(self):
        path = self.root / "software/pynq.py"
        try:
            path.write_text("raise RuntimeError('unexpected import')")
            with patch.object(runner, "load_board") as board:
                result = self.execute(run_board=True)
            board.assert_not_called()
            self.assertIn("Unlisted package files", result["error"]["message"])
        finally:
            path.unlink()

    def test_pc_board_mode_does_not_load_overlay(self):
        with patch.object(runner, "load_board") as board:
            result = self.execute(run_board=True, info_provider=lambda: {"system": "Windows", "machine": "AMD64"})
        board.assert_not_called()
        self.assertEqual(result["status"], "failed")

    def test_all_56_fake_cases_load_once_and_are_never_board_evidence(self):
        factory, loads, driver = self.fake_factory()
        result = self.execute(run_board=True, runtime_factory=factory, offline_simulation=True)
        self.assertEqual(len(loads), 1)
        self.assertEqual(len(driver.calls), 56)
        self.assertEqual(driver.calls[:2], [(0, 0, 10.0), (0, 8, 10.0)])
        self.assertEqual([c[0] for c in driver.calls[-10:]], [0, 1] * 5)
        self.assertEqual(result["status"], "offline_simulation_passed")
        self.assertFalse(result["board_validation_passed"])
        self.assertFalse(result["hardware_exercised"])
        self.assertTrue(all(c["evidence"] == "offline_simulation" and not c["is_board_measurement"]
                            for c in result["completed_cases"]))
        with zipfile.ZipFile(self.root / result["return_zip"]) as archive:
            names = archive.namelist()
            self.assertEqual(len([n for n in names if n.endswith(".csv")]), 56)
            case = json.loads(archive.read(next(n for n in names if n.endswith("001_flat_zero_core.json"))))
            self.assertEqual(len(case["records"]), 640)

    def test_baseline_gates_rest_and_preserves_actual_mismatch(self):
        factory, _, driver = self.fake_factory(fault_at=2)
        result = self.execute(run_board=True, runtime_factory=factory, offline_simulation=True)
        self.assertEqual(len(driver.calls), 2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completed_case_count"], 2)
        self.assertEqual(result["current_case"]["group"], "baseline")
        self.assertEqual(result["completed_cases"][-1]["valid_mismatches"], 1)
        self.assertIn("Traceback", result["traceback"])

    def test_stops_on_first_nonbaseline_failure(self):
        factory, _, driver = self.fake_factory(fault_at=3)
        result = self.execute(run_board=True, runtime_factory=factory, offline_simulation=True)
        self.assertEqual(len(driver.calls), 3)
        self.assertEqual(result["completed_case_count"], 3)
        self.assertFalse(result["board_validation_passed"])

    def test_strict_output_format_records_failure(self):
        factory, _, driver = self.fake_factory(fault_at=1, fault="format")
        result = self.execute(run_board=True, runtime_factory=factory, offline_simulation=True)
        self.assertEqual(len(driver.calls), 1)
        self.assertIn("exactly zero", result["completed_cases"][0]["format_error"])

    def test_interrupt_preserves_partial_results(self):
        factory, _, driver = self.fake_factory(fault_at=3, fault="interrupt")
        result = self.execute(run_board=True, runtime_factory=factory, offline_simulation=True)
        self.assertEqual(len(driver.calls), 3)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["completed_case_count"], 2)
        self.assertTrue((self.root / result["return_zip"]).is_file())

    def test_recovery_failure_retains_driver_and_calls_injected_hold(self):
        factory, _, driver = self.fake_factory(fault_at=2, fault="recovery")
        holds = []
        result = self.execute(run_board=True, runtime_factory=factory, offline_simulation=True,
                              hold_handler=lambda runtime: holds.append(runtime))
        self.assertEqual(len(driver.calls), 2)
        self.assertEqual(len(holds), 1)
        self.assertIs(holds[0].driver, driver)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["requires_board_reboot_before_python_exit"])
        self.assertFalse(result["board_validation_passed"])
        self.assertTrue((self.root / result["return_zip"]).is_file())

    def test_injection_cannot_claim_real_board_and_repeats_cannot_downgrade(self):
        factory, _, _ = self.fake_factory()
        with self.assertRaises(ValueError):
            self.execute(run_board=True, runtime_factory=factory)
        with self.assertRaises(ValueError):
            self.execute(run_board=True, repeat_frames=9)
        with self.assertRaises(ValueError):
            self.execute(timeout=float("nan"))

    def test_archive_failure_still_enters_recovery_hold(self):
        factory, _, driver = self.fake_factory(fault_at=1, fault="recovery")
        holds = []
        with patch.object(runner, "archive_results", side_effect=OSError("OFFLINE simulated disk full")):
            with self.assertRaises(OSError):
                self.execute(run_board=True, runtime_factory=factory, offline_simulation=True,
                             hold_handler=lambda runtime: holds.append(runtime))
        self.assertEqual(len(holds), 1)
        self.assertIs(holds[0].driver, driver)

    def test_recovery_hold_survives_eof_interrupt_and_broken_terminal(self):
        class Driver:
            calls = 0
            def recover(self):
                self.calls += 1
                if self.calls == 1:
                    raise RecoveryError("OFFLINE recovery still unsafe")
        driver = Driver()
        runtime = runner.BoardRuntime(object(), driver)
        with patch("builtins.input", side_effect=[EOFError(), KeyboardInterrupt(), "recover", "recover"]), \
                patch("builtins.print", side_effect=BrokenPipeError()), \
                patch.object(runner.time, "sleep", side_effect=[KeyboardInterrupt(), None]):
            runner.recovery_hold(runtime)
        self.assertEqual(driver.calls, 2)

    def test_postsuccess_diagnostic_failure_is_reported_as_failure(self):
        factory, _, driver = self.fake_factory()
        original = builtins.print
        def failed_success_print(*args, **kwargs):
            if args and str(args[0]).startswith("All 56 cases passed"):
                raise BrokenPipeError("OFFLINE simulated detached terminal")
            return original(*args, **kwargs)
        with patch("builtins.print", side_effect=failed_success_print):
            result = self.execute(run_board=True, runtime_factory=factory, offline_simulation=True)
        self.assertEqual(len(driver.calls), 56)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["board_validation_passed"])
        self.assertEqual(result["evidence"], "offline_simulation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
