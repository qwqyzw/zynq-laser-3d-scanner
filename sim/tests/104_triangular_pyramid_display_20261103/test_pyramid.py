"""Measured XYZ invariants, independent geometry, and display path regression."""
from pathlib import Path
import sys
import unittest
import importlib.util
import hashlib
import json
import csv
import time
import numpy as np

D = Path(__file__).resolve().parent
sys.path[:0] = [str(D), str(D.parent / '87_hex_prism_surface_20261024')]
from shape_models import estimate, LABELS
from triangular_pyramid import _surface_distances


def samples(base, apex, noise=.04, seed=72, faces=(0, 1, 2)):
    rng = np.random.default_rng(seed)
    points, groups = [], []
    for side in faces:
        t, u = rng.uniform(.05, .95, (2, 260))
        q = ((1-t[:, None]) * (u[:, None]*base[side] +
              (1-u[:, None])*base[(side+1) % 3]) + t[:, None]*apex)
        points.extend(q+rng.normal(0, noise, q.shape))
        groups.extend(np.arange(len(q)) % 36)
    return np.asarray(points), np.asarray(groups)


class PyramidTests(unittest.TestCase):
    def setUp(self):
        t = np.arange(3)*2*np.pi/3+.25
        self.base = np.c_[12*np.cos(t),12*np.sin(t),np.zeros(3)]
        self.apex = np.array([1.5,-1.,22.])

    def test_three_flat_sides_and_independent_geometry(self):
        p,g=samples(self.base,self.apex)
        original=p.copy()
        model=estimate(p,g,'triangular_pyramid')
        stats=model['stats']
        self.assertTrue(np.array_equal(p,original))
        self.assertEqual((len(model['vertices']),len(model['triangles'])),(4,4))
        self.assertTrue(stats['watertight'])
        self.assertEqual(stats['supported_side_count'],3)
        self.assertAlmostEqual(stats['height_estimated_mm'],22,delta=.25)
        self.assertLess(np.linalg.norm(model['vertices'][3]-self.apex),.3)
        held,_=samples(self.base,self.apex,noise=0,seed=207)
        independent=np.min(_surface_distances(held,model['vertices']),axis=1)
        self.assertLess(np.percentile(independent,95),.15)
        self.assertFalse(stats['metric_accuracy_accepted'])
        self.assertEqual(stats['new_measured_points_added'],0)

    def test_non_equilateral_base_and_offset_apex(self):
        base=np.array([[-13.,-9,0],[15.,-5,0],[-6.,14,0]])
        apex=np.array([2.,1.,27.])
        p,g=samples(base,apex,seed=816)
        m=estimate(p,g,'triangular_pyramid')
        held,_=samples(base,apex,noise=0,seed=901)
        self.assertLess(np.percentile(_surface_distances(held,m['vertices']).min(axis=1),95),.2)
        self.assertAlmostEqual(m['stats']['height_estimated_mm'],27,delta=.3)

    def test_flat_surface_is_not_fabricated_into_a_pyramid(self):
        rng=np.random.default_rng(4)
        p=np.c_[rng.uniform(-10,10,(200,2)),np.full(200,20.)]
        with self.assertRaises(ValueError):estimate(p,np.arange(200)%36,'triangular_pyramid')

    def test_insufficient_or_invalid_measurements(self):
        p,g=samples(self.base,self.apex)
        for bad,groups in [(p[:20],g[:20]),(p,np.zeros(len(p),int)),
                           (p,np.zeros(4,int)),(p*np.nan,g)]:
            with self.assertRaises(ValueError):estimate(bad,groups,'triangular_pyramid')

    def test_one_plane_does_not_constrain_three_sides(self):
        p,g=samples(self.base,self.apex,faces=(0,))
        with self.assertRaises(ValueError):estimate(p,g,'triangular_pyramid')

    def test_two_observed_sides_mark_the_missing_side_as_estimated(self):
        p,g=samples(self.base,self.apex,faces=(0,1))
        m=estimate(p,g,'triangular_pyramid')
        self.assertEqual(m['stats']['supported_side_count'],2)
        self.assertTrue(any('斜面缺少' in message for message in m['stats']['warnings']))
        self.assertEqual(m['stats']['new_measured_points_added'],0)

    def test_finite_face_distance_instead_of_plane_extension(self):
        v=np.vstack([self.base,self.apex])
        far=np.array([[80.,80.,10.]])
        self.assertGreater(float(_surface_distances(far,v).min()),60.)

    def test_saved_actual_cloud_is_immutable_and_fits(self):
        source=D.parent/'results/button_scan_received/c6bfac63dbdb427fbee7bf4bb71efe0e_8866bd36988e44f5bd6537af3475c544/viewer/points.csv'
        if not source.exists():self.skipTest('Saved measured cloud not present on this machine')
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        rows=list(csv.DictReader(source.open()))
        p=np.array([[float(r[k]) for k in ['x_mm','y_mm','z_mm']] for r in rows])
        g=np.array([int(r['point_index'])%36 for r in rows])
        started=time.monotonic();m=estimate(p,g,'triangular_pyramid')
        self.assertEqual(len(p),709)
        self.assertEqual(m['stats']['supported_side_count'],3)
        self.assertLess(m['stats']['fit_residual_p95_mm'],1.2)
        self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())
        report={'scope':'saved actual709 point replay, not a new scan or accuracy test',
                'source_csv_sha256':before,'source_run_id':'8866bd36988e44f5bd6537af3475c544',
                'runtime_s':time.monotonic()-started,'stats':m['stats'],
                'vertices':m['vertices'].tolist(),'triangles':m['triangles'].tolist()}
        (D/'evidence').mkdir(exist_ok=True)
        (D/'evidence'/'saved709_replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

    def test_ui_and_receiving_path_support_match(self):
        html=(D/'dashboard.html').read_text(encoding='utf-8')
        pc=(D/'pc_dashboard.py').read_text(encoding='utf-8')
        self.assertIn('<option value="triangular_pyramid">三棱锥</option>',html)
        self.assertIn("triangular_pyramid:'三棱锥'",html)
        self.assertIn("['pointcloud','cone','cube','hex_prism','triangular_pyramid']",pc)
        self.assertIn("['cone','cube','hex_prism','triangular_pyramid']",pc)
        self.assertIn(D.name+'/algorithm_previews/',pc)
        self.assertNotIn('89_multi_shape_display_20261024/',pc)
        self.assertIn("data.stats.shape_type==='triangular_pyramid'?faceNormals[i]",(D/'viewer_template.html').read_text(encoding='utf-8'))

    def test_manifest_and_frozen_capture_are_bound(self):
        manifest=json.loads((D/'manifest.json').read_text(encoding='utf-8'))
        for name,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((D/name).read_bytes()).hexdigest(),digest)
        self.assertEqual(hashlib.sha256((D.parent/'101_relocation_scan_20261102/manifest.json').read_bytes()).hexdigest(),manifest['capture_manifest_sha256'])
        self.assertEqual(LABELS['triangular_pyramid'],'三棱锥')


if __name__=='__main__':unittest.main()
