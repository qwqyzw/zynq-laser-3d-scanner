import unittest
import numpy as np
from fuse_measured_profiles import fuse_measured_profiles

def part(p):
 a=np.asarray(p,float).reshape(-1,2)
 return {'pixels':a,'selected':np.ones(len(a),bool),'quality_gate_passed':True}
class Priority(unittest.TestCase):
 def test_new_short_trace_cannot_displace_old_measurement(self):
  s=part([[100,41],[101,41],[102,41]]);s['baseline']=part([])
  l=part([[100,50],[101,50],[102,50]]);l['baseline']=part([[100,50],[101,50],[102,50]])
  rows,report=fuse_measured_profiles(s,l)
  self.assertEqual(len(rows),3);self.assertTrue(all(r['y_px']==50 and r['source_tier']=='baseline_min_run8' for r in rows));self.assertTrue(report['old_min_run8_preserved'])
 def test_recover_short_supported_actual_chain_and_preserve_source_index(self):
  s=part([]);s['baseline']=part([])
  l=part([[100,50],[101,50],[102,50],[110,60],[111,60],[112,60]]);l['baseline']=part([[110,60],[111,60],[112,60]])
  rows,report=fuse_measured_profiles(s,l)
  self.assertEqual(report['added_short_trace_points'],3)
  for r in rows:
   source=l['baseline'] if r['source_tier']=='baseline_min_run8' else l
   np.testing.assert_array_equal(source['pixels'][r['source_index']],[r['x_px'],r['y_px']])
if __name__=='__main__':unittest.main()
