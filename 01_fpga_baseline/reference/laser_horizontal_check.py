"""Horizontal stripe adapter for temporary camera-layout inspection only.

ROI uses original image coordinates. No geometric calibration or height output.
The vertical reference and FPGA handoff contract are unchanged.
"""
import numpy as np
from laser_vertical_fast import extract_vertical


def extract_horizontal(image, roi, threshold=5.0, min_run=8):
    x0, y0, x1, y1 = roi
    vertical = extract_vertical(
        image.transpose(1, 0, 2), roi=(y0, x0, y1, x1),
        threshold=threshold, min_run=min_run)
    return {
        "x": vertical["y"], "y": vertical["x"],
        "valid": vertical["valid"], "strength": vertical["strength"],
        "width": vertical["width"], "roi": tuple(roi),
        "short_run_columns_removed": vertical["short_run_rows_removed"],
    }


def save_horizontal_csv(path, result):
    data = np.column_stack((result["x"], result["y"],
                            result["valid"].astype(int),
                            result["strength"], result["width"]))
    np.savetxt(path, data, delimiter=",",
               fmt=["%d", "%.3f", "%d", "%.3f", "%d"],
               header="x_px,y_px,valid,signal,line_width_px", comments="")
