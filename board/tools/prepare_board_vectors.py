"""Prepare a portable static-image handoff from the committed HEX fixtures."""
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "01_fpga_baseline"
sys.path.insert(0, str(BASELINE / "tools"))
from horizontal_vectors import read_words


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="a new output directory")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(BASELINE / "manifest.json", args.output)
    shutil.copytree(BASELINE / "expected", args.output / "expected")
    vectors = args.output / "vectors"
    vectors.mkdir()
    inputs = sorted((BASELINE / "vectors").glob("*_input.hex"))
    if len(inputs) != 23:
        raise ValueError(f"Expected 23 frozen fixtures, found {len(inputs)}")
    for source in inputs:
        words = read_words(source)
        if words.shape != (640 * 480,) or (words >> 24).any():
            raise ValueError(f"Invalid packed RGB fixture: {source.name}")
        words.astype("<u4", copy=False).tofile(vectors / (source.stem + ".bin"))
    print(f"Prepared {len(inputs)} little-endian packed RGB fixtures in {args.output}")


if __name__ == "__main__":
    main()
