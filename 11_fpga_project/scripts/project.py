"""One entry point for portable Vivado builds and actual RTL simulation.

Examples: python scripts/project.py create
          python scripts/project.py sim-all
          python scripts/project.py bit --build-dir build/fresh --output-dir output/new
No action in this script connects to a board or sends motor commands.
"""
import argparse
import subprocess
import sys
from project_paths import ROOT, tool


def execute(arguments):
    print("Running:", subprocess.list2cmdline(arguments), flush=True)
    result = subprocess.run(arguments, cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)


def python_script(name, *arguments):
    execute([sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts" / name), *arguments])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "synth", "impl", "bit", "sim-project", "gui", "sim", "axi", "protocol", "extra", "sim-all", "check"])
    parser.add_argument("--variant", choices=["static", "motor_uart"], default="static",
                        help="motor_uart adds 9600 8N1 UARTLite on Arduino D1 TX / D0 RX")
    parser.add_argument("--build-dir", help="fresh build directory; relative paths are relative to 11_fpga_project")
    parser.add_argument("--output-dir", help="new bit/hwh export directory; existing pairs are never overwritten")
    args = parser.parse_args()
    if args.variant != "static" and args.action not in ("create", "synth", "impl", "bit"):
        parser.error("--variant motor_uart supports create/synth/impl/bit; open build/motor_uart/ls.xpr in Vivado for its GUI")
    if args.action in ("create", "synth", "impl", "bit"):
        (ROOT / "reports").mkdir(parents=True, exist_ok=True)
        command = [tool("vivado"), "-mode", "batch", "-source", str(ROOT / "scripts" / ("build_" + args.variant + "_overlay.tcl")),
                   "-journal", str(ROOT / "reports" / (args.variant + "_" + args.action + ".jou")),
                   "-log", str(ROOT / "reports" / (args.variant + "_" + args.action + ".log")), "-tclargs", args.action]
        if args.build_dir or args.output_dir:
            command.append(str((ROOT / (args.build_dir or ("build/" + args.variant))).resolve()))
        if args.output_dir:
            command.append(str((ROOT / args.output_dir).resolve()))
        execute(command)
    elif args.action in ("gui", "sim-project"):
        name = "open_static_gui.tcl" if args.action == "gui" else "create_project.tcl"
        command = [tool("vivado"), "-mode", "gui" if args.action == "gui" else "batch", "-source", str(ROOT / "scripts" / name)]
        execute(command)
    elif args.action == "check":
        python_script("check_project.py")
    else:
        actions = ("sim", "axi", "protocol", "extra") if args.action == "sim-all" else (args.action,)
        for action in actions:
            if action == "extra":
                python_script("generate_extra_cases.py")
                python_script("run_sim.py", "--extra")
            else:
                python_script({"sim": "run_sim.py", "axi": "run_axi_sim.py", "protocol": "run_protocol.py"}[action])


if __name__ == "__main__":
    main()
