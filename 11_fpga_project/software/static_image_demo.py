"""Static handoff image -> DMA -> laser IP -> independent expected comparison.

Run --dry-run on a PC to validate inputs without importing PYNQ.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from laser_stripe import (LaserStripe, LaserStripeError, StripeConfig,
                          INPUT_BYTES, decode_words, validate_words)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# In the complete project, use the frozen same-root runtime release by default.
if (PROJECT_ROOT.parent / "06_fpga_verified").is_dir():
    PROJECT_ROOT = PROJECT_ROOT.parent / "06_fpga_verified"


def load_sample(handoff, sample_name, mode):
    manifest = json.loads((handoff / "manifest.json").read_text(encoding="utf-8"))
    sample = next((s for s in manifest["samples"] if s["name"] == sample_name), None)
    if sample is None:
        raise ValueError(f"sample {sample_name!r} is not in manifest.json")
    stem = Path(sample_name).stem
    source = handoff / "vectors" / f"{stem}_input.bin"
    if source.stat().st_size != INPUT_BYTES:
        raise ValueError(f"input has wrong byte length: {source}")
    pixels = np.fromfile(source, dtype="<u4").astype(np.uint32, copy=False)
    validate_words(pixels)
    threshold = float(sample["threshold"]) * 256
    if not threshold.is_integer():
        raise ValueError("sample threshold is not exactly representable in Q8")
    config = StripeConfig(*sample["roi"], threshold_q8=int(threshold),
                          min_run=0 if mode == "core" else 8)
    expected_file = handoff / "expected" / f"{stem}_{mode}.hex"
    expected = np.array([int(s, 16) for s in expected_file.read_text().split()], dtype=np.uint32)
    decode_words(expected)
    return pixels, config, expected


def compare(actual, expected, tolerance=0.25):
    valid, y = decode_words(actual)
    expected_valid, expected_y = decode_words(expected)
    common = valid & expected_valid
    mismatch = int(np.count_nonzero(valid != expected_valid))
    maximum = float(np.max(np.abs(y[common] - expected_y[common]))) if np.any(common) else 0.0
    return {"valid_mismatches": mismatch, "max_coordinate_difference_px": maximum,
            "tolerance_px": tolerance, "passed": mismatch == 0 and maximum <= tolerance}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bit", type=Path, help="generated static-image .bit; paired .hwh must be adjacent")
    parser.add_argument("--ip", default="laser_stripe_0")
    parser.add_argument("--dma", default="axi_dma_0")
    parser.add_argument("--handoff", type=Path, default=PROJECT_ROOT / "handoff")
    parser.add_argument("--sample", default="flat_zero.png")
    parser.add_argument("--mode", choices=("core", "full"), default="full")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "board")
    parser.add_argument("--dry-run", action="store_true", help="validate frozen files only; no hardware access")
    args = parser.parse_args()
    pixels, config, expected = load_sample(args.handoff, args.sample, args.mode)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "sample": args.sample, "mode": args.mode,
                          "input_bytes": pixels.nbytes, "output_bytes": expected.nbytes,
                          "config": config.__dict__}, ensure_ascii=False, indent=2))
        return 0
    if args.bit is None:
        parser.error("--bit is required unless --dry-run is used")
    if not args.bit.is_file() or not args.bit.with_suffix(".hwh").is_file():
        parser.error("both .bit and matching .hwh must exist")
    from pynq import Overlay  # No PYNQ imports or board access in --dry-run.
    overlay = Overlay(str(args.bit))
    driver = LaserStripe.from_overlay(overlay, ip_name=args.ip, dma_name=args.dma,
                                      timeout=args.timeout)
    try:
        result = driver.run_words(pixels, config)
    except LaserStripeError as exc:
        print(json.dumps({"error": str(exc), "diagnostics": exc.diagnostics}, ensure_ascii=False, indent=2))
        return 2
    report = {"sample": args.sample, "mode": args.mode, "cycles": result.cycles,
              "completed_frames": result.completed_frames, "valid_columns": int(result.valid.sum()),
              **compare(result.words, expected)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_dir / f"{Path(args.sample).stem}_{args.mode}"
    np.savetxt(prefix.with_suffix(".hex"), result.words, fmt="%08X")
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
