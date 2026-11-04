"""Explicit entry points for the frozen static and integrated PL+UART releases.

Import/inspection only read ordinary files. Only load_static_runtime() and
load_integrated_runtime() import PYNQ and program the FPGA. Neither starts a
camera, sends UART commands, or runs a DMA frame. Keep the returned runtime and
reuse its driver. Never switch overlays while that driver owns buffers.
"""
from dataclasses import dataclass
import argparse
import hashlib
import json
import math
from numbers import Real
from pathlib import Path

import laser_stripe as _driver_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_active_runtime = None


class IntegrationError(RuntimeError):
    """The static release cannot be used as this project's PL runtime."""


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_artifacts(project_root=None):
    """Validate the paired release and all three driver copies, with no PYNQ.

    The release's SHA256.json stays frozen. This function checks the selected
    bit, HWH, and driver against it; verify_board.py --check-only checks the
    entire frozen dataset. The optional root is for read-only inspection of a
    copied project. Hardware loading always uses the root containing this file.
    """
    root = Path(project_root).resolve() if project_root is not None else PROJECT_ROOT
    release = root / "06_fpga_verified"
    try:
        entries = json.loads((release / "SHA256.json").read_text(encoding="utf-8-sig"))["files"]
        if not isinstance(entries, list):
            raise ValueError("files must be a list")
        by_path = {}
        for entry in entries:
            name = entry["path"]
            if not isinstance(name, str) or name in by_path:
                raise ValueError("invalid or duplicate manifest path")
            by_path[name] = entry
        frozen = {}
        for name in ("laser_static.bit", "laser_static.hwh", "software/laser_stripe.py"):
            entry = by_path[name]
            path = release / name
            if not path.is_file() or path.stat().st_size != entry["bytes"]:
                raise IntegrationError("Missing file or wrong length: " + str(path))
            actual = _digest(path)
            if actual != str(entry["sha256"]).lower():
                raise IntegrationError("Frozen release hash differs: " + str(path))
            frozen[name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": actual}
        expected_driver = frozen["software/laser_stripe.py"]["sha256"]
        copies = {}
        for role, name in (("runtime", "04_runtime/laser_stripe.py"),
                           ("release", "06_fpga_verified/software/laser_stripe.py"),
                           ("development", "11_fpga_project/software/laser_stripe.py")):
            path = root / name
            if not path.is_file() or _digest(path) != expected_driver:
                raise IntegrationError("Driver copy differs from the frozen release: " + str(path))
            copies[role] = {"path": str(path), "sha256": expected_driver}
    except IntegrationError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise IntegrationError("Cannot inspect the frozen PL release: " + str(exc)) from exc
    return {"status": "artifacts_verified", "inspection_only": True,
            "hardware_accessed": False, "project_root": str(root),
            "frozen_release": frozen, "driver_copies": copies,
            "driver_copies_identical": True, "motor_uart_available": False,
            "input_format": "640x480 uint32 0x00RRGGBB",
            "output_format": "640 uint32: valid bit31, global y Q8 bits17:0",
            "scope": "frozen static PL; no weak-mode or motor UART integration"}


@dataclass(frozen=True)
class StaticPLRuntime:
    """Keep this owner alive, including after a driver RecoveryError.

    Do not discard it or kill the kernel when DMA recovery is unconfirmed.
    Use driver.recover() and the existing driver's recovery instructions.
    """
    overlay: object
    driver: object
    artifacts: dict
    motor_uart_available: bool = False


@dataclass(frozen=True)
class IntegratedPLRuntime:
    """One overlay owns stripe extraction, DMA, and the motor UART."""
    overlay: object
    driver: object
    artifacts: dict
    motor_uart_available: bool = True


def inspect_integrated_artifacts(project_root=None):
    """Check the separately versioned UART release without accessing hardware."""
    root = Path(project_root).resolve() if project_root is not None else PROJECT_ROOT
    frozen = inspect_artifacts(root)
    release = root / "14_integrated_release_20261020"
    try:
        manifest = json.loads((release / "SHA256.json").read_text(encoding="utf-8-sig"))
        entries = {}
        for entry in manifest["files"]:
            name = entry["path"]
            path = (release / name).resolve()
            if not path.is_relative_to(release.resolve()) or name in entries:
                raise IntegrationError("Invalid integrated manifest path")
            if not path.is_file() or path.stat().st_size != entry["bytes"] or _digest(path) != entry["sha256"]:
                raise IntegrationError("Integrated release hash differs: " + str(path))
            entries[name] = dict(entry, absolute_path=str(path))
        for name in ("laser_motor.bit", "laser_motor.hwh"):
            if name not in entries:
                raise IntegrationError("Missing integrated artifact: " + name)
        sources = manifest["source_sha256"]
        for name, digest in sources.items():
            path = (root / name).resolve()
            if not path.is_relative_to(root) or _digest(path) != digest:
                raise IntegrationError("Integrated build source differs: " + name)
        for name in ("11_fpga_project/rtl/laser_stripe_core.sv",
                     "11_fpga_project/rtl/laser_stripe_axi.sv",
                     "04_runtime/laser_stripe.py"):
            if name not in sources:
                raise IntegrationError("Missing source binding: " + name)
    except IntegrationError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise IntegrationError("Cannot inspect the integrated release: " + str(exc)) from exc
    return {"status": "artifacts_verified", "inspection_only": True,
            "hardware_accessed": False, "project_root": str(root),
            "integrated_release": entries,
            "frozen_reference": frozen,
            "motor_uart_available": True,
            "motor_uart": {"ip": "axi_uartlite_0", "address": 0x42C00000,
                           "baud": 9600, "data_bits": 8, "parity": "none",
                           "tx": "Arduino D1 / U12", "rx": "Arduino D0 / T14"},
            "scope": "same-overlay stripe extraction and UART; geometry acceptance separate"}


def load_integrated_runtime(*, timeout=10.0):
    """Explicitly program the integrated release once; never move the motor.

    The turntable must be stopped and any previous DMA owner quiescent. UART
    construction/commands are deferred to the chosen motor client.
    """
    global _active_runtime
    if isinstance(timeout, bool) or not isinstance(timeout, Real) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    if _active_runtime is not None:
        raise IntegrationError("A PL runtime already exists in this kernel; reuse its driver")
    artifacts = inspect_integrated_artifacts()
    expected_driver_path = (PROJECT_ROOT / "04_runtime/laser_stripe.py").resolve()
    if Path(_driver_module.__file__).resolve() != expected_driver_path:
        raise IntegrationError("laser_stripe was imported from another project; use a clean kernel and 04_runtime")
    from pynq import Overlay
    overlay = Overlay(artifacts["integrated_release"]["laser_motor.bit"]["absolute_path"])
    if "axi_uartlite_0" not in overlay.ip_dict:
        raise IntegrationError("Integrated HWH lacks the motor UART")
    driver = _driver_module.LaserStripe.from_overlay(
        overlay, ip_name="laser_stripe_0", dma_name="axi_dma_0", timeout=float(timeout))
    _active_runtime = IntegratedPLRuntime(overlay, driver, artifacts)
    return _active_runtime


def load_static_runtime(*, timeout=10.0):
    """Explicitly load this project's 06 release once and bind its 04 driver.

    This replaces the current PL configuration. Call only with the turntable
    stopped and no live DMA/old runtime. No motor enable or motion is issued.
    Reuse the returned driver's run_words()/run_bgr() for subsequent frames.
    The frozen overlay has no motor UART, so rig_workflow.run_small_scan will
    reject it before constructing the motor controller.
    """
    global _active_runtime
    if isinstance(timeout, bool) or not isinstance(timeout, Real) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    if _active_runtime is not None:
        raise IntegrationError("A static PL runtime already exists in this kernel; reuse its driver")
    artifacts = inspect_artifacts()
    expected_driver_path = (PROJECT_ROOT / "04_runtime/laser_stripe.py").resolve()
    if Path(_driver_module.__file__).resolve() != expected_driver_path:
        raise IntegrationError("laser_stripe was imported from another project; use a clean kernel and 04_runtime")
    from pynq import Overlay  # Explicit hardware call only; never imported by inspection.
    overlay = Overlay(artifacts["frozen_release"]["laser_static.bit"]["path"])
    # Do not access overlay.axi_dma_0: native DMA construction starts channels.
    driver = _driver_module.LaserStripe.from_overlay(
        overlay, ip_name="laser_stripe_0", dma_name="axi_dma_0", timeout=float(timeout))
    _active_runtime = StaticPLRuntime(overlay, driver, artifacts)
    return _active_runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only PL release inspection; never loads an overlay")
    parser.add_argument("--integrated", action="store_true",
                        help="Inspect the separately versioned PL+UART release")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(inspect_integrated_artifacts() if args.integrated else inspect_artifacts(),
                         ensure_ascii=False, indent=2))
    except IntegrationError as exc:
        print(json.dumps({"status": "failed", "hardware_accessed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
