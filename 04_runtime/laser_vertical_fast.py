"""ROI-only, vectorized PS reference. Input is OpenCV BGR, output is pixels.

The 16-column halo preserves the old 31-column background + 3-column filter.
Missing/shadowed rows remain NaN; no interpolation or geometric calibration.
"""
import numpy as np


def _score_crop(image, roi):
    x0, y0, x1, y1 = roi
    left = max(0, x0 - 16)
    right = min(image.shape[1], x1 + 16)
    crop = image[y0:y1, left:right].astype(np.float32)
    red = crop[:, :, 2] - (crop[:, :, 0] + crop[:, :, 1]) * 0.5
    padded = np.pad(red, ((0, 0), (15, 15)), mode="edge")
    sums = np.pad(np.cumsum(padded, axis=1, dtype=np.float32),
                  ((0, 0), (1, 0)), mode="constant")
    residual = red - (sums[:, 31:] - sums[:, :-31]) / 31.0
    padded = np.pad(residual, ((0, 0), (1, 1)), mode="edge")
    score = (padded[:, :-2] + 2 * residual + padded[:, 2:]) / 4.0
    return score[:, x0-left:x1-left]


def keep_long_runs(valid, min_run):
    """Reject short contiguous runs, without joining either side of a gap."""
    if min_run <= 1:
        return valid.copy()
    edges = np.diff(np.r_[False, valid, False].astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    stops = np.flatnonzero(edges == -1)
    difference = np.zeros(len(valid) + 1, dtype=np.int32)
    keep = stops - starts >= min_run
    difference[starts[keep]] = 1
    difference[stops[keep]] = -1
    return np.cumsum(difference[:-1]) > 0


def extract_vertical(image, background=None, roi=(290, 95, 325, 450),
                     threshold=5.0, min_run=8):
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Expected a BGR colour image")
    h, w = image.shape[:2]
    if len(roi) != 4 or any(int(v) != v for v in roi):
        raise ValueError("ROI must contain four integer pixel coordinates")
    roi = tuple(map(int, roi))
    x0, y0, x1, y1 = roi
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise ValueError("ROI must be inside the image: x0,y0,x1,y1")
    if not np.isfinite(threshold) or threshold < 0:
        raise ValueError("THRESHOLD must be finite and nonnegative")
    if int(min_run) != min_run or min_run < 0:
        raise ValueError("MIN_RUN must be a nonnegative integer")
    score = _score_crop(image, roi)
    if background is not None:
        if background.shape != image.shape:
            raise ValueError("Background and input sizes differ")
        score = score - _score_crop(background, roi)

    rows = np.arange(y1-y0)
    peaks = np.argmax(score, axis=1)
    values = score[rows, peaks]
    cutoffs = np.maximum(1.5, values * 0.45)
    offsets = np.arange(-8, 9)
    indices = peaks[:, None] + offsets
    in_bounds = (indices >= 0) & (indices < x1-x0)
    neighbours = score[rows[:, None], np.clip(indices, 0, x1-x0-1)]
    above = in_bounds & (neighbours >= cutoffs[:, None])
    # Walk outward simultaneously for every row; stop at the first weak pixel.
    left_count = np.logical_and.accumulate(above[:, 7::-1], axis=1).sum(axis=1)
    right_count = np.logical_and.accumulate(above[:, 9:], axis=1).sum(axis=1)
    widths_roi = left_count + right_count + 1
    eligible = (values >= threshold) & (peaks > 0) & (peaks < x1-x0-1)
    connected = (offsets[None, :] >= -left_count[:, None]) & (
        offsets[None, :] <= right_count[:, None])
    weights = np.maximum(neighbours - cutoffs[:, None], 0) * connected
    totals = weights.sum(axis=1)
    accepted = eligible & (widths_roi >= 2) & (widths_roi <= 14) & (totals > 0)
    numerator = (indices * weights).sum(axis=1, dtype=np.float64)
    centres = x0 + numerator / np.where(totals > 0, totals, 1)

    xs = np.full(h, np.nan, dtype=np.float32)
    strength = np.zeros(h, dtype=np.float32)
    widths = np.zeros(h, dtype=np.int32)
    valid = np.zeros(h, dtype=bool)
    strength[y0:y1] = values
    widths[y0:y1] = np.where(eligible, widths_roi, 0)
    xs[y0:y1] = np.where(accepted, centres, np.nan)
    valid[y0:y1] = accepted
    count = np.convolve(valid.astype(np.int32), np.ones(5, dtype=np.int32), mode="full")[2:2+h]
    valid &= count >= 3
    candidate_count = int(valid.sum())
    valid = keep_long_runs(valid, int(min_run))
    xs[~valid] = np.nan
    return {"x": xs, "y": np.arange(h), "valid": valid,
            "strength": strength, "width": widths, "score_roi": score,
            "roi": roi, "short_run_rows_removed": candidate_count-int(valid.sum())}


def save_csv(path, result):
    data = np.column_stack((result["y"], result["x"], result["valid"].astype(int),
                            result["strength"], result["width"]))
    np.savetxt(path, data, delimiter=",", fmt=["%d", "%.3f", "%d", "%.3f", "%d"],
               header="y_px,x_px,valid,signal,line_width_px", comments="")
