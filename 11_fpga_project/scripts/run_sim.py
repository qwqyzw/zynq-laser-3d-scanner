"""Compile actual RTL and compare 46 frozen cases or 20 extra directed cases."""
from pathlib import Path
import argparse
import json
import subprocess
import sys
import time
from project_paths import ROOT, baseline_root, refresh_sim_header, sim_path, tool

BUILD = ROOT / "build/sim"


def run(name, args, timeout=3600, working_directory=BUILD):
    working_directory.mkdir(parents=True, exist_ok=True)
    (ROOT / "reports").mkdir(parents=True, exist_ok=True)
    log_path = ROOT / "reports" / name
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(args, cwd=working_directory, stdout=log,
                                stderr=subprocess.STDOUT, timeout=timeout)
    print(json.dumps({"stage": name, "returncode": result.returncode,
                      "seconds": round(time.monotonic() - started, 2)}), flush=True)
    if result.returncode:
        print(log_path.read_text(encoding="utf-8", errors="replace")[-8000:])
        raise SystemExit(result.returncode)
    return log_path


def compare_extra(output):
    sys.path.insert(0, str(baseline_root() / "tools"))
    from horizontal_vectors import compare, read_words
    rows = []
    for entry in json.loads((ROOT / "results/extra/manifest.json").read_text(encoding="utf-8")):
        filename = entry["name"] + "_" + entry["mode"] + ".hex"
        report, _ = compare(read_words(output / filename),
                            read_words(ROOT / "results/extra/expected" / filename), .25)
        rows.append({"sample": entry["name"], "mode": entry["mode"], **report})
    summary = {"passed": all(row["passed"] for row in rows), "compared_cases": len(rows), "results": rows}
    (ROOT / "reports/extra_comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if not summary["passed"]:
        raise SystemExit("Directed RTL comparison failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-compile", action="store_true")
    parser.add_argument("--extra", action="store_true")
    args = parser.parse_args()
    limit = args.limit if args.limit is not None else (20 if args.extra else 46)
    if limit < 1:
        parser.error("--limit must be positive")
    data = ROOT / "results/extra" if args.extra else baseline_root()
    casefile = ROOT / ("tb/extra_cases.txt" if args.extra else "tb/cases.txt")
    output = ROOT / ("results/extra/actual" if args.extra else "results/rtl_out")
    BUILD.mkdir(parents=True, exist_ok=True)
    refresh_sim_header()
    output.mkdir(parents=True, exist_ok=True)
    (BUILD / "run_limit.txt").write_text(str(limit), encoding="ascii")
    (BUILD / "run_paths.txt").write_text(" ".join(sim_path(path, BUILD) for path in
        (casefile, data / "vectors", output)), encoding="ascii")
    if not args.skip_compile:
        run("rtl_compile.log", [tool("xvlog"), "--sv", "-i", str(ROOT / "tb"),
            str(ROOT / "rtl/laser_stripe_core.sv"), str(ROOT / "tb/tb_laser_stripe.sv")], 120)
        run("rtl_elaborate.log", [tool("xelab"), "tb_laser_stripe", "-s", "laser_stripe_sim"], 180)
    log = run("extra_simulation.log" if args.extra else "rtl_simulation.log",
              [tool("xsim"), "laser_stripe_sim", "-runall"])
    if "RTL_SIMULATION_PASSED" not in log.read_text(encoding="utf-8", errors="replace"):
        raise SystemExit("Missing simulation pass marker")
    if args.extra and limit >= 20:
        compare_extra(output)
    elif not args.extra and limit >= 46:
        run("batch_comparison.log", [sys.executable, "-B", "-X", "utf8",
            str(baseline_root() / "tools/batch_compare.py"), str(output), "--mode", "both",
            "--out", str(ROOT / "results/comparison")], 120)


if __name__ == "__main__":
    main()
