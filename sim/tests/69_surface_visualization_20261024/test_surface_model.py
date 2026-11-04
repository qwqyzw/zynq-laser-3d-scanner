"""Synthetic geometry tests only; real scan geometry is processed on Zynq."""
import unittest
import numpy as np
from surface_model import estimate

def cube():
    a=np.linspace(-5,5,8);out=[]
    for side in [-5,5]:
        for x in a:
            for y in a:out.extend([[side,x,y],[x,side,y],[x,y,side]])
    return np.unique(out,axis=0)

class Checks(unittest.TestCase):
    def test_closed_mesh_and_outward_orientation(self):
        m=estimate(cube());edges={}
        for f in m['triangles']:
            a,b,c=m['vertices'][f];self.assertGreater(np.cross(b-a,c-a)@((a+b+c)/3),0)
            for i,j in [(f[0],f[1]),(f[1],f[2]),(f[2],f[0])]:
                e=tuple(sorted((int(i),int(j))));edges[e]=edges.get(e,0)+1
        self.assertTrue(all(v==2 for v in edges.values()))
        self.assertAlmostEqual(m['stats']['estimated_volume_mm3'],1000)
    def test_outlier_removed_and_remaining_samples_not_moved(self):
        p=np.vstack([cube(),[500,500,500]]);m=estimate(p)
        self.assertFalse(m['accepted'][-1]);self.assertTrue(np.array_equal(p[m['kept_input_rows']],m['cleaned']))
    def test_duplicate_traceability(self):
        p=cube();m=estimate(np.vstack([p,p]))
        self.assertEqual(m['stats']['duplicate_points'],len(p));self.assertEqual(m['accepted'].sum(),len(p))
    def test_planar_sparse_and_nonfinite_fail(self):
        planar=np.column_stack([np.arange(40),np.arange(40)%5,np.zeros(40)])
        for p in [planar,np.zeros((3,3)),np.full((30,3),np.nan)]:
            with self.assertRaises(ValueError):estimate(p)
    def test_mesh_vertices_exactly_reference_retained_measurements(self):
        m=estimate(cube());s={tuple(p) for p in m['cleaned']}
        self.assertTrue(all(tuple(p) in s for p in m['vertices']))
        self.assertTrue(m['stats']['all_faces_are_derived']);self.assertFalse(m['stats']['metric_accuracy_accepted'])
if __name__=='__main__':unittest.main(verbosity=2)
