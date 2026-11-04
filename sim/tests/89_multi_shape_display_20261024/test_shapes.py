import unittest,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'87_hex_prism_surface_20261024'))
from shape_models import estimate
class Shapes(unittest.TestCase):
 def test_cube_preserves_points_and_equal_edges(self):
  rng=np.random.default_rng(12);p=rng.uniform(-10,10,(900,3));which=np.arange(900)%3;p[np.arange(900),which]=np.where(np.arange(900)%2,10,-10);p[:,2]+=40;p+=rng.normal(0,.06,p.shape);old=p.copy();m=estimate(p,np.arange(len(p))%36,'cube')
  self.assertTrue(np.array_equal(p,old));self.assertEqual(len(m['triangles']),12);self.assertTrue(m['stats']['watertight']);self.assertAlmostEqual(m['stats']['edge_estimated_mm'],20,delta=.5)
 def test_cone_preserves_points_and_closed_mesh(self):
  rng=np.random.default_rng(4);z=rng.uniform(0,30,1000);t=rng.uniform(0,2*np.pi,len(z));r=(30-z)*.5;p=np.c_[r*np.cos(t),r*np.sin(t),z]+rng.normal(0,.04,(len(z),3));old=p.copy();m=estimate(p,np.arange(len(p))%36,'cone')
  self.assertTrue(np.array_equal(p,old));self.assertTrue(m['stats']['watertight']);self.assertAlmostEqual(m['stats']['top_estimated_z_mm'],30,delta=.5);self.assertLess(m['stats']['fit_residual_p95_mm'],.3)
 def test_hex_prism_is_closed(self):
  t=np.arange(6)*np.pi/3;poly=np.c_[10*np.cos(t),10*np.sin(t)];rng=np.random.default_rng(8);xy=rng.uniform(-10,10,(10000,2));from scipy.spatial import Delaunay
  xy=xy[Delaunay(poly).find_simplex(xy)>=0][:2000];p=np.c_[xy,np.full(len(xy),20.)];m=estimate(p,np.arange(len(p))%36,'hex_prism');self.assertTrue(m['stats']['watertight']);self.assertEqual(len(m['vertices']),12)
 def test_empty_or_wrong_shape_rejected(self):
  for typ in ['cube','cone','hex_prism','unknown']:
   with self.assertRaises(ValueError):estimate(np.zeros((0,3)),np.zeros(0,int),typ)
if __name__=='__main__':unittest.main()
