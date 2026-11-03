import unittest
import numpy as np
from temporal_consensus import combine_reviewed

class Checks(unittest.TestCase):
 def run_policy(self,y):return combine_reviewed(y,[int(np.isfinite(a).sum()) for a in y],[{}]*3,(0,0,640,480))
 def scene(self):
  y=np.full((3,640),np.nan);y[:,100:110]=120;return y
 def test_missing_frame_recovers_only_actual_supported_measurements(self):
  y=self.scene();y[1,200:208]=150.25;y[2,200:208]=150.75;p,r=self.run_policy(y)
  self.assertEqual(r['common_quality_columns'],10);self.assertEqual(len(p),18)
  self.assertTrue(np.all(p[10:,1]==150.25));self.assertEqual(r['two_frame_support'][0]['source_frame'],1)
 def test_no_pair_can_replace_failed_original_gate(self):
  y=self.scene();y[:,109]=np.nan;y[1:,200:220]=150;p,r=self.run_policy(y)
  self.assertFalse(r['image_quality_gate_passed']);self.assertEqual(len(p),9)
 def test_singletons_short_chains_and_jumps_are_rejected(self):
  y=self.scene();y[1:,200:202]=150;y[0,230:240]=150;y[1:,250:254]=[100,120,100,120];p,r=self.run_policy(y)
  self.assertEqual(len(p),10)
 def test_pair_disagreement_and_three_frame_outlier_rejected(self):
  y=self.scene();y[1,200:210]=150;y[2,200:210]=152;y[:,220:230]=[[100],[100],[120]];p,r=self.run_policy(y)
  self.assertEqual(len(p),10)
 def test_mixed_pair_chain_cannot_supply_support(self):
  y=self.scene()
  for x in range(200,210):y[[0,1] if x%2 else [1,2],x]=150
  p,r=self.run_policy(y);self.assertEqual(len(p),10)
 def test_consistent_three_frames_unchanged_and_invalid_input_rejected(self):
  y=self.scene();p,r=self.run_policy(y);self.assertEqual(len(p),10);self.assertEqual(r['added_two_frame_columns'],0)
  y[0,1]=float('inf')
  with self.assertRaises(ValueError):self.run_policy(y)

if __name__=='__main__':unittest.main(verbosity=2)
