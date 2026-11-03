"""Fit an upright tetrahedral display prior; input XYZ is never changed."""
import numpy as np
from scipy.optimize import least_squares


def _vertices(parameters, base):
    cx, cy, dx, dy, height, angle = parameters[:6]
    radii = np.asarray(parameters[6:])
    if len(radii) == 1:
        radii = np.repeat(radii, 3)
    theta = angle + np.arange(3) * (2.0 * np.pi / 3.0)
    polygon = np.c_[cx + radii * np.cos(theta), cy + radii * np.sin(theta)]
    return np.vstack([np.c_[polygon, np.full(3, base)], [cx + dx, cy + dy, base + height]])


def _plane_distances(points, vertices):
    """Outward signed distances to the three sides and horizontal base."""
    interior = vertices.mean(axis=0)
    planes = []
    for face in ([0, 1, 3], [1, 2, 3], [2, 0, 3], [0, 2, 1]):
        a, b, c = vertices[face]
        normal = np.cross(b - a, c - a)
        length = np.linalg.norm(normal)
        if length < 1e-8:
            raise ValueError('三棱锥拟合退化，原始点云保留。')
        normal /= length
        if (interior - a) @ normal > 0:
            normal *= -1
        planes.append((points - a) @ normal)
    return np.asarray(planes).T


def _surface_distances(points, vertices):
    """Euclidean distances to finite triangular faces, not infinite planes."""
    distances = []
    for face in ([0, 1, 3], [1, 2, 3], [2, 0, 3], [0, 2, 1]):
        a, b, c = vertices[face]
        u, v = b - a, c - a
        normal = np.cross(u, v)
        normal /= np.linalg.norm(normal)
        signed = (points - a) @ normal
        projection = points - signed[:, None] * normal
        w = projection - a
        uu, uv, vv = u @ u, u @ v, v @ v
        determinant = uu * vv - uv * uv
        alpha = ((w @ u) * vv - (w @ v) * uv) / determinant
        beta = ((w @ v) * uu - (w @ u) * uv) / determinant
        inside = (alpha >= 0) & (beta >= 0) & (alpha + beta <= 1)
        edge_distances = []
        for x, y in [(a, b), (b, c), (c, a)]:
            edge = y - x
            fraction = np.clip((points - x) @ edge / (edge @ edge), 0., 1.)
            edge_distances.append(np.linalg.norm(points - x - fraction[:, None] * edge, axis=1))
        distances.append(np.where(inside, abs(signed), np.min(edge_distances, axis=0)))
    return np.asarray(distances).T


def triangular_pyramid_fit(points, groups):
    """Three planar sides and one triangle at the calibrated table plane z=0.

    A regular triangular seed is refined to an asymmetric triangle and offset
    apex. Missing base/side regions remain explicitly estimated. Fits with no
    useful height variation or only one observed side are rejected.
    """
    p = np.asarray(points, dtype=float)
    g = np.asarray(groups, dtype=int)
    low, top = np.percentile(p[:, 2], [1, 99])
    span_z = float(top - low)
    xy_lo, xy_hi = np.percentile(p[:, :2], [1, 99], axis=0)
    width = float(max(np.max(xy_hi - xy_lo), 1.0))
    if span_z < max(1.0, width * .045) or top < 1.0:
        raise ValueError('测点高度变化不足以约束三棱锥斜面；请检查实测点云和激光覆盖。')
    # The reference plane is a stated prior, not an added bottom measurement.
    base = 0.0
    if top <= base + 1.0 or low < -max(3.0, span_z):
        raise ValueError('三棱锥测点与台面参考高度不一致，请检查本轮几何。')
    centre = (xy_lo + xy_hi) / 2.0
    height0 = max(float(top + span_z * .10), 2.0)
    initial_radius = float(np.clip(width * .72, 1.0, width * 2.0))
    height_min = max(float(top - min(.4, span_z * .025)), base + 1.0)
    height_max = float(top + span_z * 1.5)
    radius_min, radius_max = width * .15, width * 2.5
    common_lo = [centre[0] - width, centre[1] - width,
                 -width, -width, height_min]
    common_hi = [centre[0] + width, centre[1] + width,
                 width, width, height_max]
    # Deterministic bounded subsampling keeps file-only fitting practical on ARM.
    sample = np.linspace(0, len(p) - 1, min(len(p), 2400), dtype=int)
    fit_points = p[sample]
    scale = max(.15, min(.8, width * .02))

    def residual(v):
        distances = _plane_distances(fit_points, _vertices(v, base))
        return np.max(distances, axis=1)

    seeds = []
    for angle in np.arange(4) * np.pi / 6:
        initial = [*centre, 0., 0., height0, angle, initial_radius]
        lo = common_lo + [angle - np.pi / 3, radius_min]
        hi = common_hi + [angle + np.pi / 3, radius_max]
        fit = least_squares(residual, initial, bounds=(lo, hi),
                            loss='soft_l1', f_scale=scale, max_nfev=110,
                            x_scale='jac')
        seeds.append((float(np.mean(np.minimum(abs(residual(fit.x)), width))), fit.x))
    candidates = []
    for _, seed in sorted(seeds, key=lambda x: x[0])[:2]:
        initial = np.r_[seed[:6], np.repeat(seed[6], 3)]
        lo = common_lo + [seed[5] - np.pi / 3] + [radius_min] * 3
        hi = common_hi + [seed[5] + np.pi / 3] + [radius_max] * 3
        fit = least_squares(residual, initial, bounds=(lo, hi),
                            loss='soft_l1', f_scale=scale, max_nfev=160,
                            x_scale='jac')
        candidates.append((float(np.mean(np.minimum(abs(residual(fit.x)), width))), fit))
    _, result = min(candidates, key=lambda x: x[0])
    vertices = _vertices(result.x, base)
    distances = _surface_distances(p, vertices)
    residuals = np.min(distances, axis=1)
    nearest = np.argmin(distances, axis=1)
    tolerance = max(.5, width * .035)
    support = []
    group_support = []
    minimum = max(10, int(len(p) * .035))
    for face in range(3):
        mask = (nearest == face) & (residuals <= tolerance)
        support.append(int(mask.sum()))
        group_support.append(int(len(np.unique(g[mask]))))
    supported = [i for i in range(3) if support[i] >= minimum and group_support[i] >= 2]
    if len(supported) < 2:
        raise ValueError('至少需要两个斜面的有效测点来约束三棱锥；本轮仅保留真实点云。')
    edge1, edge2 = vertices[1, :2] - vertices[0, :2], vertices[2, :2] - vertices[0, :2]
    base_area = float(abs(edge1[0] * edge2[1] - edge1[1] * edge2[0]) / 2.)
    if base_area < width * width * .025:
        raise ValueError('三角形底面拟合退化；请增加斜面测点后重试。')
    height = float(vertices[3, 2] - base)
    warnings = []
    if len(supported) < 3:
        warnings.append('有一个斜面缺少足够实测支持，按三棱锥先验估计；请切换点云核对。')
    if float(top - low) / height < .45:
        warnings.append('实测高度覆盖有限，尖顶和底面位置包含较多外推。')
    faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]], dtype=int)
    parameters = {
        'height_estimated_mm': height, 'bottom_estimated_z_mm': base,
        'top_estimated_z_mm': float(vertices[3, 2]),
        'apex_estimated_xyz_mm': vertices[3].tolist(),
        'base_triangle_estimated_xyz_mm': vertices[:3].tolist(),
        'side_measured_support_counts': support,
        'side_measured_angle_counts': group_support,
        'supported_side_count': len(supported),
        'base_is_reference_plane_prior': True,
        'triangular_faces_are_flat': True,
        'fit_method': 'bounded_robust_three_side_plane_fit',
        'dimensions_note': '估计高度 %.2f mm · 三个平整斜面' % height,
        'assumption': '立放三棱锥先验；三角形底面取台面z=0，三个斜面与顶点由测点拟合，缺测区域为算法估计。',
        'fit_specific_warnings': warnings,
    }
    return vertices, faces, residuals, parameters
