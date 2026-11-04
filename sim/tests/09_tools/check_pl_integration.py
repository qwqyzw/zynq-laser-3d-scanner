"""Offline checks for the complete project's explicit static PL entry point.

No hardware operation is available in this checker. Tests use a fake pynq
module, temporary copied release files, and block camera/motor constructors.
"""
from pathlib import Path
import argparse
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "04_runtime"))
import pl_runtime
import laser_stripe


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pl_entry_test_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        names = ("06_fpga_verified/SHA256.json", "06_fpga_verified/laser_static.bit",
                 "06_fpga_verified/laser_static.hwh", "06_fpga_verified/software/laser_stripe.py",
                 "04_runtime/laser_stripe.py", "11_fpga_project/software/laser_stripe.py")
        for name in names:
            dest = self.root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        self.calls = {"overlays": [], "mmio": [], "writes": [], "allocations": 0}
        self.identity = {laser_stripe.ID: laser_stripe.IP_ID, laser_stripe.VERSION: laser_stripe.IP_VERSION,
                         laser_stripe.REG_INPUT_BYTES: laser_stripe.INPUT_BYTES,
                         laser_stripe.REG_OUTPUT_BYTES: laser_stripe.OUTPUT_BYTES,
                         laser_stripe.REG_WIDTH: laser_stripe.WIDTH, laser_stripe.REG_HEIGHT: laser_stripe.HEIGHT}
        calls, identity = self.calls, self.identity

        class FakeOverlay:
            def __init__(self, path):
                calls["overlays"].append(path)
                self.ip_dict = {
                    "laser_stripe_0": {"phys_addr": 0x43C00000, "addr_range": 65536},
                    "axi_dma_0": {"phys_addr": 0x40400000, "addr_range": 65536,
                                  "parameters": {"C_INCLUDE_SG": "0", "C_SG_LENGTH_WIDTH": "23",
                                                 "C_ADDR_WIDTH": "32", "C_INCLUDE_MM2S": "1", "C_INCLUDE_S2MM": "1"}}}

            def __getattr__(self, name):
                if name == "axi_dma_0":
                    raise AssertionError("The native DMA object must not be constructed")
                raise AttributeError(name)

        class FakeMMIO:
            def __init__(self, address, length):
                calls["mmio"].append((address, length))
                self.address = address

            def read(self, offset):
                if self.address != 0x43C00000:
                    raise AssertionError("No DMA reads should occur during binding")
                return identity[offset]

            def write(self, offset, value):
                calls["writes"].append((offset, value))
                raise AssertionError("No hardware writes during driver binding")

        def allocate(**kwargs):
            calls["allocations"] += 1
            raise AssertionError("The entry point must not allocate DMA buffers")

        self.fake_pynq = ModuleType("pynq")
        self.fake_pynq.Overlay = FakeOverlay
        self.fake_pynq.MMIO = FakeMMIO
        self.fake_pynq.allocate = allocate
        self.addCleanup(setattr, pl_runtime, "_active_runtime", None)

    def load_fake(self):
        with patch.object(pl_runtime, "PROJECT_ROOT", self.root), \
             patch.object(pl_runtime._driver_module, "__file__", str(self.root / "04_runtime/laser_stripe.py")), \
             patch.dict(sys.modules, {"pynq": self.fake_pynq}):
            return pl_runtime.load_static_runtime()

    def test_import_and_inspection_never_import_pynq(self):
        code = ("import sys;sys.path.insert(0," + repr(str(ROOT / "04_runtime")) + ");"
                "sys.modules['pynq']=None;import pl_runtime;"
                "assert pl_runtime.inspect_artifacts()['driver_copies_identical'];"
                "assert sys.modules['pynq'] is None")
        subprocess.run([sys.executable, "-B", "-c", code], check=True, capture_output=True, text=True, timeout=30)
        self.assertEqual(pl_runtime.inspect_artifacts(self.root)["hardware_accessed"], False)

    def test_explicit_load_uses_one_paired_release_and_no_native_dma(self):
        runtime = self.load_fake()
        self.assertIsInstance(runtime.driver, laser_stripe.LaserStripe)
        self.assertEqual(self.calls["overlays"], [str(self.root / "06_fpga_verified/laser_static.bit")])
        self.assertEqual(self.calls["mmio"], [(0x40400000, 65536), (0x43C00000, 65536)])
        self.assertEqual(self.calls["writes"], [])
        self.assertEqual(self.calls["allocations"], 0)
        self.assertFalse(runtime.motor_uart_available)
        with self.assertRaises(pl_runtime.IntegrationError):
            self.load_fake()
        self.assertEqual(len(self.calls["overlays"]), 1)

    def test_missing_hwh_rejects_before_pynq(self):
        (self.root / "06_fpga_verified/laser_static.hwh").unlink()
        with patch.object(pl_runtime, "PROJECT_ROOT", self.root), patch.dict(sys.modules, {"pynq": None}):
            with self.assertRaises(pl_runtime.IntegrationError):
                pl_runtime.load_static_runtime()

    def test_tampered_bit_rejects_before_pynq(self):
        path = self.root / "06_fpga_verified/laser_static.bit"
        content = bytearray(path.read_bytes()); content[-1] ^= 1; path.write_bytes(content)
        with patch.object(pl_runtime, "PROJECT_ROOT", self.root), patch.dict(sys.modules, {"pynq": None}):
            with self.assertRaises(pl_runtime.IntegrationError):
                pl_runtime.load_static_runtime()

    def test_each_driver_copy_must_match_frozen_release(self):
        for name in ("04_runtime/laser_stripe.py", "06_fpga_verified/software/laser_stripe.py",
                     "11_fpga_project/software/laser_stripe.py"):
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b"\n# simulated drift\n")
            with self.subTest(name=name), self.assertRaises(pl_runtime.IntegrationError):
                pl_runtime.inspect_artifacts(self.root)
            path.write_bytes(original)

    def test_wrong_project_import_rejects_before_pynq(self):
        with patch.object(pl_runtime, "PROJECT_ROOT", self.root), patch.dict(sys.modules, {"pynq": None}):
            with self.assertRaisesRegex(pl_runtime.IntegrationError, "another project"):
                pl_runtime.load_static_runtime()

    def test_invalid_timeout_rejects_before_pynq(self):
        for value in (0, -1, float("inf"), float("nan"), True, "10"):
            with self.subTest(timeout=value), patch.dict(sys.modules, {"pynq": None}), self.assertRaises(ValueError):
                pl_runtime.load_static_runtime(timeout=value)

    def test_missing_uart_blocks_before_camera_or_motor(self):
        from rig_workflow import run_small_scan
        output = self.root / "must_not_be_created"
        with patch("rig_workflow.ArduinoAngleController", side_effect=AssertionError("Motor constructed")), \
             patch("rig_workflow.StripeCheckedExposureCamera", side_effect=AssertionError("Camera constructed")):
            with self.assertRaisesRegex(RuntimeError, "lacks old motor UART"):
                run_small_scan(SimpleNamespace(), output, (160, 215, 410, 270),
                               client_session_date="2026-10-20", backend="PL", pl_driver=object())
        self.assertFalse(output.exists())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-only", action="store_true", help="Only inspect frozen bit/HWH and three driver hashes")
    args = parser.parse_args(argv)
    try:
        inspection = pl_runtime.inspect_artifacts()
    except pl_runtime.IntegrationError as exc:
        print(json.dumps({"offline_only": True, "all_passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    tests_run = 0
    if not args.inspect_only:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(EntryPointTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        tests_run = result.testsRun
        if not result.wasSuccessful():
            return 1
    print(json.dumps({"offline_only": True, "real_hardware_tested": False, "all_passed": True,
                      "entry_point_tests": tests_run, "inspection": inspection}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
