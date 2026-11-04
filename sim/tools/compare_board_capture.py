"""Recheck saved board output against independent frozen Q8 reference."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "01_fpga_baseline"
sys.path.insert(0, str(BASELINE / "tools"))
from horizontal_vectors import compare, read_words

ARCHIVE = ROOT / "evidence/static_board_56_frames.zip"
EXPECTED_ARCHIVE_SHA256 = "0f9e5452e774672ba5e1fa1e4d0df86cfa15d74b2eecaf35af58aaf86750a8da"
TOLERANCE_PX = 0.25
CAPTURE_NAME = re.compile(r"(\d{3})_([a-z0-9_]+)_(core|full)\.hex")


def verify(archive):
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    if archive_sha256 != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("Saved board capture archive SHA-256 differs")
    baseline = json.loads((BASELINE / "manifest.json").read_text(encoding="utf-8"))
    frozen_cases = [(Path(sample["name"]).stem, mode)
                    for sample in baseline["samples"] for mode in ("core", "full")]
    planned = frozen_cases + [("flat_zero", "full"), ("flat_laser_off", "full")] * 5
    rows = []
    with ZipFile(archive) as saved:
        if saved.testzip() is not None:
            raise ValueError("ZIP CRC check failed")
        members = sorted(item for item in saved.namelist() if item.endswith(".hex"))
        if len(members) != len(planned):
            raise ValueError("Wrong saved board case count")
        for ordinal, (member, wanted) in enumerate(zip(members, planned), start=1):
            match = CAPTURE_NAME.fullmatch(Path(member).name)
            if not match or int(match[1]) != ordinal or match.groups()[1:] != wanted:
                raise ValueError(f"Unexpected board case at ordinal {ordinal}")
            words = saved.read(member).decode("ascii").split()
            if len(words) != 640 or any(not re.fullmatch(r"[0-9a-fA-F]{8}", word) for word in words):
                raise ValueError(f"Invalid Q8 records in {member}")
            actual = np.asarray([int(word, 16) for word in words], dtype=np.uint32)
            expected = read_words(BASELINE / "expected" / ("_".join(wanted) + ".hex"))
            result, _ = compare(actual, expected, TOLERANCE_PX)
            rows.append({"ordinal": ordinal, "sample": wanted[0], "mode": wanted[1], **result})
    return {
        "scope": "offline replay of 2026-10-20 PYNQ-Z2 static board capture",
        "new_board_access": False,
        "archive_sha256": archive_sha256,
        "cases": len(rows),
        "valid_mismatches": sum(len(row["valid_mismatch_columns"]) for row in rows),
        "max_coordinate_difference_px": max(row["max_common_error_px"] or 0.0 for row in rows),
        "tolerance_px": TOLERANCE_PX,
        "passed": all(row["passed"] for row in rows),
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="optional detailed JSON output file")
    args = parser.parse_args()
    result = verify(ARCHIVE)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
