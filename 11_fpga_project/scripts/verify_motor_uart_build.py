"""Validate the exported UART overlay, frozen-core compatibility and route audit.

Read-only apart from writing reports/motor_uart/motor_uart_build_verification.json.
No hardware imports or network access.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from project_paths import ROOT


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def modules(path):
    root = ET.parse(path).getroot()
    result = {}
    for module in root.findall(".//MODULE"):
        result[module.get("INSTANCE")] = {
            "attributes": module.attrib,
            "parameters": {p.get("NAME"): p.get("VALUE")
                           for p in module.findall("./PARAMETERS/PARAMETER")},
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/motor_uart")
    args = parser.parse_args()
    output = ROOT / args.output_dir
    reports = ROOT / "reports/motor_uart"
    bit = output / "laser_motor_uart.bit"
    hwh = output / "laser_motor_uart.hwh"
    current = modules(hwh)
    baseline = modules(ROOT / "output/static/laser_static.hwh")
    checks = []

    def check(condition, text):
        checks.append({"check": text, "passed": bool(condition)})
        if not condition:
            raise ValueError(text)

    for name in ("processing_system7_0", "axi_dma_0", "laser_stripe_0"):
        check(current[name]["parameters"] == baseline[name]["parameters"],
              name + ": all HWH parameters equal released static overlay")
    uart = current["axi_uartlite_0"]
    check(uart["attributes"]["VLNV"] == "xilinx.com:ip:axi_uartlite:2.0", "UART VLNV")
    expected = {"C_BAUDRATE": "9600", "C_DATA_BITS": "8", "C_USE_PARITY": "0",
                "C_S_AXI_ACLK_FREQ_HZ": "50000000", "C_BASEADDR": "0x42C00000",
                "C_HIGHADDR": "0x42C0FFFF"}
    for key, value in expected.items():
        check(uart["parameters"].get(key) == value, key + "=" + value)
    source_paths = [
        "11_fpga_project/rtl/laser_stripe_core.sv",
        "11_fpga_project/rtl/laser_stripe_axi.sv",
        "04_runtime/laser_stripe.py",
        "11_fpga_project/scripts/build_motor_uart_overlay.tcl",
        "11_fpga_project/scripts/audit_motor_uart_overlay.tcl",
        "11_fpga_project/scripts/verify_motor_uart_build.py",
        "11_fpga_project/scripts/project.py",
        "11_fpga_project/scripts/run_motor_uart_build.ps1",
        "11_fpga_project/constraints/motor_uart.xdc",
        "11_fpga_project/third_party/pynq_z2/ps7_settings.tcl",
        "11_fpga_project/third_party/pynq_z2/base_official.xdc",
    ]
    source_sha256 = {name: sha(ROOT.parent / name) for name in source_paths}
    check(source_sha256[source_paths[0]] ==
          "161bf3b5e63a20a0b229a0c7cbf76b7e47d61734e12c8815d3369d360d0d778c",
          "frozen core RTL unchanged")
    check(source_sha256[source_paths[1]] ==
          "4395e28c589982d011c576bfaf3f541a0998424b4e280eeb86f6499a17ca91db",
          "frozen AXI RTL unchanged")
    check(source_sha256[source_paths[2]] == sha(ROOT / "software/laser_stripe.py") ==
          sha(ROOT.parent / "06_fpga_verified/software/laser_stripe.py"),
          "three laser drivers identical")
    provenance = json.loads((ROOT / "third_party/pynq_z2/base_xdc_provenance.json").read_text())
    check(source_sha256[source_paths[-1]] == provenance["sha256"], "official pin source hash")
    official = (ROOT / "third_party/pynq_z2/base_official.xdc").read_text()
    for index, pin in ((0, "T14"), (1, "U12")):
        check(f"PACKAGE_PIN {pin} IOSTANDARD LVCMOS33" in
              next(line for line in official.splitlines()
                   if f"arduino_gpio_tri_io[{index}]" in line),
              "official Arduino D%d = %s" % (index, pin))
    audit = (reports / "motor_uart_hardware_audit.txt").read_text()
    for line in ("MOTOR_UART_PHYSICAL_AUDIT_PASS",
                 "UART RX=Arduino D0=T14 IN LVCMOS33 pullup=1",
                 "UART TX=Arduino D1=U12 OUT LVCMOS33",
                 "DRC_errors=0; DRC_critical_warnings=0"):
        check(line in audit, "route audit: " + line)
    timing = {name: float(re.search(name + r"=([-0-9.]+)", audit).group(1))
              for name in ("setup_slack_ns", "hold_slack_ns")}
    for name, value in timing.items():
        check(value >= 0, name + " nonnegative")
    timing_checks = (reports / "motor_uart_impl_check_timing.rpt").read_text()
    check("checking unconstrained_internal_endpoints (0)" in timing_checks,
          "zero unconstrained internal endpoints")
    utilization = (reports / "motor_uart_impl_utilization.rpt").read_text()
    resources = {}
    for name in ("Slice LUTs", "Slice Registers", "Block RAM Tile", "DSPs", "Bonded IOB"):
        match = re.search(r"^\|\s*" + re.escape(name) + r"\*?\s*\|\s*(\S+)", utilization, re.M)
        if match:
            resources[name] = float(match.group(1))
    check({"Slice LUTs", "Slice Registers", "Block RAM Tile", "DSPs"} <= set(resources),
          "implemented utilization report contains main resource counts")
    check(resources["Block RAM Tile"] <= 140, "implemented BRAM fits device")
    artifact_sha256 = {path.name: sha(path) for path in (bit, hwh)}
    check(artifact_sha256[bit.name] != sha(ROOT / "output/static/laser_static.bit"),
          "new bitstream differs from static release")
    report = {
        "status": "passed", "scope": "local implemented hardware; board validation separate",
        "variant": "motor_uart", "part": "xc7z020clg400-1", "fabric_clock_hz": 50000000,
        "uart": {"instance": "axi_uartlite_0", "base": "0x42C00000", "range": 65536,
                 "baudrate": 9600, "data_bits": 8, "parity": "none", "stop_bits": 1,
                 "tx": {"arduino": "D1", "pin": "U12", "direction": "OUT"},
                 "rx": {"arduino": "D0", "pin": "T14", "direction": "IN"},
                 "iostandard": "LVCMOS33"},
        "artifact_sha256": artifact_sha256,
        "artifact_sizes": {path.name: path.stat().st_size for path in (bit, hwh)},
        "source_sha256": source_sha256, "timing": timing, "resources": resources,
        "asynchronous_timing_boundary": {
            "rx": "motor_uart_rx has no synchronous input delay; false path to UART IP synchronizer",
            "tx": "motor_uart_tx has no synchronous output delay; false path at board boundary",
            "unconstrained_internal_endpoints": 0,
        },
        "checks": checks, "drc_errors": 0, "drc_critical_warnings": 0,
        "known_build_messages": [
            "PSU-1/PSU-2 critical warnings from unchanged official 16-bit DDR preset lane2/3 skew values",
            "Generated AXI ID width warnings also present in previous static architecture",
        ],
    }
    target = reports / "motor_uart_build_verification.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "checks": len(checks), "report": str(target),
                      "sha256": artifact_sha256, "timing": timing, "resources": resources},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
