"""Present an existing board XYZ CSV as four equal-scale scientific PNG views.

Only display projection is performed. This tool does not reconstruct, align,
filter object/background, fit, or change the supplied XYZ coordinates. Pillow
is the sole non-standard dependency. A source/output fingerprint report is
written beside the PNG.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from render_object_preview import finite_points


PALETTE = {"background": "#ffffff", "foreground": "#20333e", "muted": "#62727b",
           "grid": "#dde5e9", "points": "#087e9b", "warning": "#a22525",
           "warning_background": "#fff0ed"}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_font(requested=None):
    choices = [Path(requested)] if requested else []
    choices += [Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/arial.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for candidate in choices:
        if candidate.is_file():
            return str(candidate)
    raise ValueError("No TrueType font found; supply --font")


def alignment_failed(metadata):
    failures = []
    def visit(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                field = str(key).casefold()
                here = (path + "." + field).strip(".")
                is_alignment = "alignment" in here or "对齐" in here or "拼接" in here
                result_field = (field in ("passed", "accepted", "verified", "valid", "gate")
                                or field.endswith(("_passed", "_accepted", "_verified", "_valid", "_validated"))
                                or field in ("alignment_gate", "experimental_alignment_gate"))
                if is_alignment and result_field and item is False:
                    failures.append(here + "=false")
                visit(item, here)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, path + f"[{index}]")
        elif isinstance(value, str):
            lower = value.casefold()
            related = ("alignment" in path or "对齐" in path or "拼接" in path
                       or "alignment" in lower or "对齐" in value or "拼接" in value)
            failed = any(word in lower for word in ("failed", "failure", "rejected", "失败", "未通过", "不通过"))
            if related and failed:
                failures.append(path + ": " + value)
    visit(metadata)
    return failures


def plot(csv_path, meta_path, output, *, font_path=None, max_points=None, columns=None):
    csv_path, meta_path, output = Path(csv_path).resolve(), Path(meta_path).resolve(), Path(output).resolve()
    input_sha = {"csv": sha256(csv_path), "metadata": sha256(meta_path)}
    points, stats = finite_points(csv_path, columns)
    metadata = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    units = metadata.get("units")
    if not isinstance(units, str) or not units.strip():
        raise ValueError("metadata.units must specify the displayed coordinate unit")
    units = units.strip()
    axis_units = metadata.get("axis_units", {axis: units for axis in "xyz"})
    if not isinstance(axis_units, dict) or any(axis_units.get(axis) != units for axis in "xyz"):
        raise ValueError("All three axis units must equal metadata.units")
    if max_points is not None and max_points <= 0:
        raise ValueError("--max-points must be positive")
    shown = points
    if max_points is not None and len(points) > max_points:
        shown = [points[index * len(points) // max_points] for index in range(max_points)]
    bounds = [[min(p[axis] for p in points), max(p[axis] for p in points)] for axis in range(3)]
    centres = [(low + high) / 2 for low, high in bounds]
    common_span = max(high - low for low, high in bounds) or 1.0
    # All axes and all panels use the same coordinate-to-pixel scale. The 2-D
    # projection below is a display transform only; source XYZ are not changed.
    visible_span = common_span * 1.12
    font_file = find_font(font_path)
    fonts = {size: ImageFont.truetype(font_file, size) for size in (18, 21, 23, 25, 31, 36)}
    image = Image.new("RGB", (1900, 1800), PALETTE["background"])
    drawing = ImageDraw.Draw(image)
    def text(x, y, value, size=23, fill="foreground", anchor=None):
        drawing.text((x, y), str(value), font=fonts[size], fill=PALETTE.get(fill, fill), anchor=anchor)
    def truncate(value, size, width):
        value = str(value)
        while value and drawing.textlength(value, font=fonts[size]) > width:
            value = value[:-2]
        return value
    title = metadata.get("title", "Board-generated measured point cloud")
    text(70, 30, truncate(title, 36, 1750), 36)
    accuracy_accepted = metadata.get("metric_accuracy_accepted") is True
    scale = str(metadata.get("scale_status", "nominal"))
    actual_label = f"Actual finite points: {len(points):,}"
    if len(shown) != len(points):
        actual_label += f" | Displayed sample: {len(shown):,}"
    text(70, 86, f"{actual_label}  |  Scale: {scale} {units}  |  Equal XYZ scale", 25)
    geometry_label = str(metadata.get("geometry_status", "Metric accuracy not accepted"))
    text(70, 125, truncate(geometry_label, 23, 1750), 23, "muted")
    failures = alignment_failed(metadata)
    test_only = metadata.get("synthetic") is True or metadata.get("export_test_only") is True or metadata.get("test_only") is True
    if failures or test_only:
        drawing.rectangle((60, 165, 1840, 207), fill=PALETTE["warning_background"])
        banner = ("TOOL CHECK ONLY — synthetic data; not a real object scan" if test_only
                  else "Experimental alignment gate failed — measured points shown without correction")
        text(75, 171, banner, 23, "warning")
    top = 230 if failures or test_only else 200
    box_size = 530
    pixels_per_unit = box_size / visible_span
    dot = 1.7 if len(shown) <= 15000 else 1.2
    def panel(origin, axes, title):
        x, y = origin
        plot_x, plot_y = x + 95, y + 47
        text(plot_x + box_size / 2, y + 5, title, 25, anchor="mt")
        a, b = axes
        lo_a, lo_b = centres[a] - visible_span / 2, centres[b] - visible_span / 2
        for index in range(5):
            offset = index * box_size / 4
            gx, gy = plot_x + offset, plot_y + box_size - offset
            drawing.line((gx, plot_y, gx, plot_y + box_size), fill=PALETTE["grid"], width=1)
            drawing.line((plot_x, gy, plot_x + box_size, gy), fill=PALETTE["grid"], width=1)
            text(gx, plot_y + box_size + 8, f"{lo_a + index * visible_span / 4:.1f}", 18, "muted", "mt")
            text(plot_x - 10, gy, f"{lo_b + index * visible_span / 4:.1f}", 18, "muted", "rm")
        drawing.rectangle((plot_x, plot_y, plot_x + box_size, plot_y + box_size), outline=PALETTE["muted"], width=1)
        for point in shown:
            sx = plot_x + (point[a] - lo_a) * pixels_per_unit
            sy = plot_y + box_size - (point[b] - lo_b) * pixels_per_unit
            drawing.ellipse((sx-dot, sy-dot, sx+dot, sy+dot), fill=PALETTE["points"])
        text(plot_x + box_size / 2, plot_y + box_size + 37, f"{'XYZ'[a]} ({units})", 23, anchor="mt")
        text(plot_x, plot_y - 8, f"{'XYZ'[b]} ({units})", 23, anchor="lb")
    panel((110, top), (0, 1), "Top view · XY")
    panel((1020, top), (0, 2), "Front view · XZ")
    panel((110, top + 680), (1, 2), "Side view · YZ")
    # Oblique orthographic rendering has the same mm/pixel factor as the other
    # views, with equal-length unit X/Y/Z vectors before screen projection.
    oblique_x, oblique_y = 1115, top + 827
    text(oblique_x + box_size / 2, top + 685, "Oblique view · equal-scale projection", 25, anchor="mt")
    yaw, pitch = -.65, .45
    ca, sa, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    def project(point):
        dx, dy, dz = (point[index] - centres[index] for index in range(3))
        u, v = dx * ca - dy * sa, dx * sa + dy * ca
        # All outputs are screen display values, never stored as object XYZ.
        return (oblique_x + box_size / 2 + u * pixels_per_unit,
                oblique_y + box_size / 2 - (dz * cp - v * sp) * pixels_per_unit,
                v * cp + dz * sp)
    for sx, sy, depth in sorted((project(point) for point in shown), key=lambda item: item[2]):
        drawing.ellipse((sx-dot, sy-dot, sx+dot, sy+dot), fill=PALETTE["points"])
    base = [bounds[axis][0] for axis in range(3)]
    start = project(base)
    for axis in range(3):
        endpoint = list(base)
        endpoint[axis] += common_span * .24
        end = project(endpoint)
        drawing.line((start[0], start[1], end[0], end[1]), fill=PALETTE["muted"], width=2)
        text(end[0] + 5, end[1], "XYZ"[axis], 21, "muted")
    footer_y = 1715
    metric_label = "Metric accuracy accepted" if accuracy_accepted else "Metric accuracy NOT accepted; mm are nominal"
    text(70, footer_y, f"Measured vertices only; no surface filling. {metric_label}.", 21, "warning" if not accuracy_accepted else "muted")
    text(70, footer_y + 31, "Input CSV SHA-256: " + input_sha["csv"], 18, "muted")
    if sha256(csv_path) != input_sha["csv"] or sha256(meta_path) != input_sha["metadata"]:
        raise RuntimeError("Input changed during presentation rendering")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")
    report = {"presentation_only": True, "coordinate_computation_performed": False,
              "coordinate_modification_performed": False, "alignment_applied": False,
              "surface_fitted": False, "actual_finite_points": len(points), "displayed_points": len(shown),
              "bounds": bounds, "units": units, "axis_units": axis_units,
              "same_scale_all_xyz_axes_and_views": True,
              "experimental_alignment_gate_failed": bool(failures), "alignment_failure_metadata": failures,
              "metric_accuracy_accepted": accuracy_accepted, "scale_status": scale,
              "test_only": test_only, "input_sha256": input_sha,
              "output_sha256": sha256(output), "output_size": [1900, 1800],
              "invalid_csv_rows_not_displayed": stats["skipped_invalid_rows"]}
    output.with_suffix(".presentation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("meta", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", type=Path)
    parser.add_argument("--max-points", type=int)
    parser.add_argument("--x-column")
    parser.add_argument("--y-column")
    parser.add_argument("--z-column")
    args = parser.parse_args()
    columns = (args.x_column, args.y_column, args.z_column)
    if any(columns) and not all(columns):
        parser.error("Specify all three coordinate columns together")
    report = plot(args.csv, args.meta, args.output, font_path=args.font,
                  max_points=args.max_points, columns=columns if all(columns) else None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
