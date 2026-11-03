import unittest
from scene_limits import evaluate_patch
class Scene(unittest.TestCase):
 def test_original_failure_now_demo_passes(self):
  p=evaluate_patch(.807997,2.236068,.4377);self.assertTrue(p['passed']);self.assertFalse(p['strict_passed'])
 def test_too_much_shift_fails(self):self.assertFalse(evaluate_patch(.95,4.01,.2)['passed'])
 def test_weak_match_fails(self):self.assertFalse(evaluate_patch(.749,1,.2)['passed'])
 def test_ambiguous_reference_still_fails(self):self.assertFalse(evaluate_patch(.95,1,.019)['passed'])
 def test_nonfinite_fails(self):self.assertFalse(evaluate_patch(float('nan'),1,.5)['passed'])
 def test_original_good_still_good(self):self.assertTrue(evaluate_patch(.95,1,.4)['strict_passed'])
if __name__=='__main__':unittest.main()
