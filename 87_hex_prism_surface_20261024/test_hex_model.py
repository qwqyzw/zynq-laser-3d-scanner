import unittest
import numpy as np
from hex_model import estimate
from hex_surface import check_request

def fixture():
 rng=np.random.default_rng(8701);poly=np.array([[-8,-5],[2,-7],[9,-3],[10,4],[2,8],[-7,5]],float);points=[];groups=[]
 for g in range(36):
  theta=np.deg2rad(g*10);d=np.array([np.cos(theta),np.sin(theta)]);plus=[];minus=[]
  for a,b in zip(poly,np.roll(poly,-1,axis=0)):
   edge=b-a;n=np.array([edge[1],-edge[0]]);c=n@a;den=n@d
   if den>1e-9:plus.append(c/den)
   elif den< -1e-9:minus.append(c/den)
  lo=max(minus);hi=min(plus)
  for t in np.linspace(lo,hi,37):points.append([*(d*t+rng.normal(0,.07,2)),23.5+rng.normal(0,.15)]);groups.append(g)
  for t in [lo,hi]:
   for z in [4.,9.,14.,19.]:points.append([*(d*t+rng.normal(0,.07,2)),z+rng.normal(0,.1)]);groups.append(g)
 return np.array(points),np.array(groups),poly

class Checks(unittest.TestCase):
 def test_irregular_hex_prism_planar_and_vertical(self):
  points,groups,poly=fixture();saved=points.copy();m=estimate(points,groups);v=m['vertices'];self.assertEqual(v.shape,(12,3));self.assertEqual(m['triangles'].shape,(20,3));np.testing.assert_array_equal(points,saved)
  np.testing.assert_allclose(v[:6,:2],v[6:,:2]);np.testing.assert_allclose(v[:6,2],0);self.assertLess(np.ptp(v[6:,2]),1e-12);self.assertLess(abs(v[6,2]-23.5),.3)
  nearest=np.linalg.norm(v[6:,:2,None]-poly.T[None,:,:],axis=1).min(axis=1);self.assertLess(nearest.max(),1.2)
  for f in m['triangles'][8:]:
   a,b,c=v[f];normal=np.cross(b-a,c-a);self.assertLess(abs(normal[2]),1e-9)
  self.assertTrue(m['stats']['watertight']);self.assertFalse(m['stats']['regular_hexagon_assumed']);self.assertFalse(m['stats']['metric_accuracy_accepted'])
 def test_independent_angle_holdout_and_actual_samples_preserved(self):
  p,g,_=fixture();m=estimate(p,g);self.assertTrue(m['stats']['display_fit_validation']['passed']);self.assertEqual(m['stats']['display_fit_validation']['heldout_angle_groups'],[0,5,10,15,20,25,30,35])
  original={tuple(row) for row in p};self.assertTrue(all(tuple(row) in original for row in m['cleaned']));self.assertEqual(m['stats']['new_measured_points_added'],0);self.assertTrue(m['stats']['bottom_assumed_not_measured'])
 def test_insufficient_and_collinear_input_rejected(self):
  with self.assertRaises(ValueError):estimate(np.zeros((10,3)),np.zeros(10,int))
  p=np.c_[np.linspace(-10,10,360),np.zeros(360),np.full(360,20.)]
  with self.assertRaises(ValueError):estimate(p,np.arange(360)%36)
 def test_request_cannot_apply_to_other_scan_or_prior_shape(self):
  r={'object':'hexagonal_prism','vertical_side_completion_authorized':True,'run_id':'a'*32,'session_id':'b'*32,'baseline_PL_counter':1207,'capture_manifest_sha256':'pin','geometry_sha256':'geometry'};t=dict(r,release_manifest_sha256='pin');self.assertTrue(check_request(r,t))
  for edits in [{'run_id':'c'*32},{'session_id':'d'*32},{'baseline_PL_counter':1208},{'geometry_sha256':'other'}]:
   with self.assertRaises(ValueError):check_request(r,{**t,**edits})
  with self.assertRaises(ValueError):check_request({**r,'object':'triangular_pyramid'},t)
if __name__=='__main__':unittest.main(verbosity=2)
