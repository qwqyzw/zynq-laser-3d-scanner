"""Exercise actual AXI-Lite wrapper/core, then compare its frozen-image outputs."""
import argparse
import json
import sys
from project_paths import ROOT, baseline_root, refresh_sim_header, sim_path, tool
from run_sim import run

BUILD = ROOT / "build/axi_sim"
OUT = ROOT / "results/axi_out"


def compare_outputs():
    sys.path.insert(0, str(baseline_root() / "tools"))
    from horizontal_vectors import compare, read_words
    rows = []
    for mode in ("core", "full"):
        filename = "flat_zero_" + mode + ".hex"
        report, _ = compare(read_words(OUT / filename), read_words(baseline_root() / "expected" / filename), .25)
        rows.append({"sample": "flat_zero", "mode": mode, **report})
    result = {"passed": all(row["passed"] for row in rows), "compared_cases": len(rows), "results": rows,
              "note": "Actual RTL output; expected files are read only after simulation."}
    (ROOT / "reports/axi_comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    if not result["passed"]:
        raise SystemExit("Frozen output comparison failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-compile", action="store_true")
    args = parser.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    refresh_sim_header()
    OUT.mkdir(parents=True, exist_ok=True)
    (BUILD / "run_paths.txt").write_text(" ".join(sim_path(path, BUILD) for path in
        (baseline_root() / "vectors", OUT)), encoding="ascii")
    def stage(name, command, timeout=3600):
        return run("axi_" + name + ".log", command, timeout, BUILD)
    if not args.skip_compile:
        stage("compile", [tool("xvlog"), "--sv", "-i", str(ROOT / "tb"), str(ROOT / "rtl/laser_stripe_core.sv"),
              str(ROOT / "rtl/laser_stripe_axi.sv"), str(ROOT / "tb/tb_laser_axi.sv")], 180)
        stage("elaborate", [tool("xelab"), "tb_laser_axi", "-s", "laser_axi_sim"], 180)
    log = stage("simulation", [tool("xsim"), "laser_axi_sim", "-runall"])
    contents = log.read_text(encoding="utf-8", errors="replace")
    if "AXI_SUITE_PASSED" not in contents:
        print(contents[-8000:])
        raise SystemExit("Missing AXI simulation pass marker")
    compare_outputs()


if __name__ == "__main__":
    main()
