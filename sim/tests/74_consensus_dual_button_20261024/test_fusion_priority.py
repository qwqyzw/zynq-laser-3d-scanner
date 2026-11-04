import unittest
import numpy as np
from fuse_measured_profiles import fuse_measured_profiles

def part(points,temporal=()):
 p=np.array(points,float).reshape(-1,2)
 return {'pixels':p,'selected':np.ones(len(p),bool),'quality_gate_passed':True,'temporal_columns':list(temporal)}

class Checks(unittest.TestCase):
 def test_weak_short_conflict_cannot_remove_existing_strict_long(self):
  short=part([(x,110) for x in range(20,23)],range(20,23));long=part([(x,100) for x in range(20,23)])
  rows,r=fuse_measured_profiles(short,long)
  self.assertEqual(len(rows),3);self.assertTrue(all(v['source_exposure']==20 for v in rows))
 def test_supported_temporal_points_added_without_interpolation(self):
  rows,r=fuse_measured_profiles(part([]),part([(x,100+x*.25) for x in range(20,28)],range(20,28)))
  self.assertEqual(len(rows),8);self.assertEqual(r['interpolated_points'],0)
 def test_real_strict_conflict_still_rejected(self):
  rows,r=fuse_measured_profiles(part([(x,110) for x in range(20,23)]),part([(x,100) for x in range(20,23)]))
  self.assertEqual(rows,[])
 def test_unsupported_temporal_singleton_not_added(self):
  rows,r=fuse_measured_profiles(part([]),part([(20,100)],(20,)))
  self.assertEqual(rows,[])
if __name__=='__main__':unittest.main(verbosity=2)
