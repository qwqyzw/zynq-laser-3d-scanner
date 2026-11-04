"""Relocatable paths shared by the complete-handoff FPGA tools; no board access."""
from pathlib import Path
import os
import shutil

ROOT = Path(__file__).resolve().parents[1]


def baseline_root():
    for candidate in (ROOT.parent / "01_fpga_baseline", ROOT / "handoff"):
        if (candidate / "manifest.json").is_file() and (candidate / "vectors").is_dir():
            return candidate
    raise FileNotFoundError("Frozen baseline missing; keep 11_fpga_project beside 01_fpga_baseline")


def vivado_bin():
    candidates = []
    for key in ("VIVADO_BIN", "VIVADO_ROOT", "XILINX_VIVADO"):
        if os.environ.get(key):
            path = Path(os.environ[key])
            candidates += [path, path / "bin"]
    found = shutil.which("vivado") or shutil.which("vivado.bat")
    if found:
        candidates.append(Path(found).parent)
    for parent in (Path("D:/AMDDesignTools"), Path("C:/AMDDesignTools"),
                   Path("C:/Xilinx/Vivado"), Path("/tools/Xilinx/Vivado")):
        if parent.is_dir():
            candidates += sorted(parent.glob("*/bin"), reverse=True)
            candidates += sorted(parent.glob("*/Vivado/bin"), reverse=True)
    for directory in candidates:
        if any((directory / name).is_file() for name in ("vivado.bat", "vivado")):
            return directory
    raise FileNotFoundError("Vivado not found. Set VIVADO_BIN to its bin directory or put Vivado on PATH.")


def tool(name):
    directory = vivado_bin()
    for suffix in ((".bat", "") if os.name == "nt" else ("", ".bat")):
        path = directory / (name + suffix)
        if path.is_file():
            return str(path)
    raise FileNotFoundError("Missing Vivado tool: " + name)


def sim_path(path, working_directory):
    """SV fscanf uses whitespace-delimited paths, so use relative ASCII names."""
    value = Path(os.path.relpath(Path(path), working_directory)).as_posix()
    if any(character.isspace() for character in value):
        raise ValueError("Simulation relative path contains whitespace: " + value)
    return value


def docs_root():
    for candidate in (ROOT / "docs", ROOT / "evidence_20261019/docs"):
        if (candidate / "寄存器表.md").is_file():
            return candidate
    raise FileNotFoundError("FPGA documentation missing")


def refresh_sim_header():
    text = []
    for name, path in (("LASER_PROJECT_ROOT", ROOT), ("LASER_BASELINE_ROOT", baseline_root())):
        text += ["`ifndef " + name, '`define ' + name + ' "' + path.as_posix() + '"', "`endif"]
    (ROOT / "tb/project_paths.svh").write_text("\n".join(text) + "\n", encoding="utf-8")
