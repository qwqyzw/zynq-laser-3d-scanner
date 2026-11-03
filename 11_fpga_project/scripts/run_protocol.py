"""Actual RTL stream-protocol and recovery tests; no hardware access."""
from project_paths import ROOT, tool
from run_sim import run


def main():
    run("protocol_compile.log", [tool("xvlog"), "--sv", str(ROOT / "rtl/laser_stripe_core.sv"),
        str(ROOT / "tb/tb_laser_protocol.sv")], 120)
    run("protocol_elaborate.log", [tool("xelab"), "tb_laser_protocol", "-s", "laser_protocol_sim"], 180)
    log = run("protocol_simulation.log", [tool("xsim"), "laser_protocol_sim", "-runall"])
    if "PROTOCOL_SUITE_PASSED" not in log.read_text(encoding="utf-8", errors="replace"):
        raise SystemExit("Missing protocol pass marker")


if __name__ == "__main__":
    main()
