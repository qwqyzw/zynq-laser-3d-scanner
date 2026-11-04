"""104 adds a pyramid prior while preserving frozen89 primitive implementations."""
from pathlib import Path
import hashlib
import importlib.util
import sys
import numpy as np

P = Path(__file__).resolve().parent.parent
legacy_path = P / '89_multi_shape_display_20261024' / 'shape_models.py'
if hashlib.sha256(legacy_path.read_bytes()).hexdigest() != '8a90f90ec8e6848e09952c0fec1ca0dfeadb99e0b3231467b9117aca79a24532':
    raise ValueError('Frozen89 primitive source changed')
sys.path.insert(0, str(P / '87_hex_prism_surface_20261024'))
spec = importlib.util.spec_from_file_location('frozen_shape_models89_for104', legacy_path)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
LABELS = {**legacy.LABELS, 'triangular_pyramid': '三棱锥'}


def estimate(points, groups, shape):
    if shape != 'triangular_pyramid':
        return legacy.estimate(points, groups, shape)
    from triangular_pyramid import triangular_pyramid_fit
    p = np.asarray(points, float)
    g = np.asarray(groups, int)
    original = p.copy()
    if (p.ndim != 2 or p.shape[1] != 3 or not 30 <= len(p) <= 50000
            or g.shape != (len(p),) or not np.isfinite(p).all()):
        raise ValueError('Need30..50000 finite measured points and matching angle groups')
    q, keep = legacy.clean(p)
    cg = g[keep]
    if len(np.unique(cg)) < 3:
        raise ValueError('Need at least three measured angles')
    vertices, faces, residuals, params = triangular_pyramid_fit(q, cg)
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    if not np.isfinite(vertices).all() or not (counts == 2).all():
        raise ValueError('Estimated pyramid mesh must be finite and closed')
    volume = float(np.sum(np.einsum('ij,ij->i', vertices[faces[:, 0]],
                       np.cross(vertices[faces[:, 1]], vertices[faces[:, 2]]))) / 6.)
    if abs(volume) < 1e-5:
        raise ValueError('Estimated pyramid has zero volume')
    if volume < 0:
        faces = faces[:, [0, 2, 1]]
    p95 = float(np.percentile(residuals, 95))
    warnings = list(params.pop('fit_specific_warnings'))
    if p95 > 3:
        warnings.append('测点与三棱锥拟合偏差较大；模型仅作外形预览，请核对点云。')
    if len(np.unique(cg)) < 30:
        warnings.append('部分角度缺测，缺失区域由指定形状先验估计。')
    assert np.array_equal(original, p)
    stats = {
        'raw_points': len(p), 'foreground_points': len(p), 'denoised_points': len(q),
        'mesh_vertices': len(vertices), 'mesh_faces': len(faces), 'boundary_edges': 0,
        'watertight': True, 'closed_for_display_only': True,
        'shape_type': shape, 'shape_label': LABELS[shape],
        'shape_prior_explicitly_selected': True,
        'fit_residual_median_mm': float(np.median(residuals)),
        'fit_residual_p95_mm': p95, 'display_fit_check_passed': p95 <= 3,
        'raw_points_modified': False, 'measured_points_moved': False,
        'new_measured_points_added': 0, 'all_faces_are_estimates': True,
        'metric_accuracy_accepted': False, 'complete_shape_accuracy_verified': False,
        'warnings': warnings, **params,
    }
    return {'vertices': vertices, 'triangles': faces, 'cleaned': q,
            'weak_faces': np.ones(len(faces), int), 'stats': stats}
