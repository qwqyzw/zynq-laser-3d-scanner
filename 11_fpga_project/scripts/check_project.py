"""Check the transplanted static PL artifacts and host interface without a board.

This is an integrity/metadata check; it is not a new synthesis or board test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree as ET
from project_paths import ROOT, baseline_root


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_pair(directory):
    bit = directory / "laser_static.bit"
    hwh = directory / "laser_static.hwh"
    manifest_path = directory / "build_manifest.json"
    if not bit.is_file() or bit.stat().st_size < 1000000 or not hwh.is_file():
        raise ValueError("Paired static bit/hwh missing: " + str(directory))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    listed = {row["file"]: row for row in manifest["files"]}
    for path in (bit, hwh):
        if path.name not in listed or digest(path) != listed[path.name]["sha256"]:
            raise ValueError("Build artifact differs from paired manifest: " + path.name)
        if path.stat().st_size != listed[path.name]["bytes"]:
            raise ValueError("Artifact length differs: " + path.name)
    for entry in manifest.get("source_hashes", []):
        if entry["file"].startswith("rtl/"):
            if digest(ROOT / entry["file"]) != entry["sha256"]:
                raise ValueError("RTL differs from validated bit: " + entry["file"])
    xml = ET.parse(hwh)
    params = {}
    for instance in ("axi_dma_0", "laser_stripe_0", "processing_system7_0"):
        module = xml.find(".//MODULE[@INSTANCE='" + instance + "']")
        if module is None:
            raise ValueError("HWH lacks instance " + instance)
        params[instance] = {node.get("NAME").lower(): node.get("VALUE")
                            for node in module.findall("./PARAMETERS/PARAMETER")}
    expected_dma = {"c_include_sg": 0, "c_sg_length_width": 23, "c_addr_width": 32,
                    "c_include_mm2s": 1, "c_include_s2mm": 1,
                    "c_include_mm2s_dre": 0, "c_include_s2mm_dre": 0}
    for name, value in expected_dma.items():
        if int(params["axi_dma_0"][name], 0) != value:
            raise ValueError("Incompatible DMA parameter " + name)
    if float(params["processing_system7_0"]["pcw_fpga0_peripheral_freqmhz"]) != 50:
        raise ValueError("FCLK0 is not 50 MHz")
    ranges = {node.get("INSTANCE"): (int(node.get("BASEVALUE"), 0), int(node.get("HIGHVALUE"), 0))
              for node in xml.findall(".//MEMRANGE") if node.get("MEMTYPE") == "REGISTER"}
    expected_ranges = {"axi_dma_0": (0x40400000, 0x4040FFFF), "laser_stripe_0": (0x43C00000, 0x43C0FFFF)}
    for name, interval in expected_ranges.items():
        if ranges.get(name) != interval:
            raise ValueError("Wrong address range " + name)
    return {"bit_sha256": digest(bit), "hwh_sha256": digest(hwh), "paired_manifest_passed": True,
            "rtl_matches_validated_bit": True, "fclk0_mhz": 50, "dma_parameters": expected_dma,
            "addresses": {name: [hex(low), hex(high)] for name, (low, high) in expected_ranges.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-tests", action="store_true", help="also run 24 driver and 14 runner unit tests")
    args = parser.parse_args()
    baseline = baseline_root()
    samples = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))["samples"]
    if len(samples) != 23:
        raise ValueError("Expected 23 frozen samples")
    for sample in samples:
        stem = Path(sample["name"]).stem
        if (baseline / "vectors" / (stem + "_input.bin")).stat().st_size != 1228800:
            raise ValueError("Wrong input size " + stem)
        for mode in ("core", "full"):
            if len((baseline / "expected" / (stem + "_" + mode + ".hex")).read_text().split()) != 640:
                raise ValueError("Wrong expected size " + stem)
    result = {"passed": True, "board_access": False, "check_scope": "integrity_and_metadata_only",
              "baseline": str(baseline), "frozen_samples": len(samples), "frozen_cases": 2 * len(samples),
              "pair": inspect_pair(ROOT / "output/static")}
    mapping = json.loads((ROOT / "software/register_map.json").read_text(encoding="utf-8"))
    if mapping["clock_target_hz"] != 50000000 or mapping["local_address_bits"] != 16 or mapping["mapped_window_bytes"] != 65536:
        raise ValueError("Host register ABI mismatch")
    if args.offline_tests:
        command = [sys.executable, "-B", "-X", "utf8", "-m", "unittest", "test_driver", "test_verify_board"]
        process = subprocess.run(command, cwd=ROOT / "software", capture_output=True, text=True, encoding="utf-8")
        print(process.stdout + process.stderr)
        if process.returncode:
            raise SystemExit(process.returncode)
        result["offline_unit_tests_passed"] = True
    (ROOT / "reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports/project_integrity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
