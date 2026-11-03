import unittest
import numpy as np
from foreground_select import select_foreground
class Foreground(unittest.TestCase):
 def test_larger_background_cannot_replace_central_object(self):
  rng=np.random.RandomState(110);central=rng.normal(0,.5,(90,3));central[:,2]+=8;background=rng.normal(0,.5,(200,3))+[40,0,45];extra=np.array([[.2,.2,8.2],[55,0,53.]])
  p=np.r_[central,background,extra];before=p.copy();g=np.arange(len(p))%12;r=np.linalg.norm(p[:,:2],axis=1);b=np.r_[np.ones(290,bool),[False,False]];keep,stats=select_foreground(p,g,r,b)
  self.assertTrue(keep[:90].all());self.assertFalse(keep[90:290].any());self.assertTrue(keep[-2]);self.assertFalse(keep[-1]);np.testing.assert_array_equal(p,before)
 def test_single_angle_baseline_is_not_accepted_as_object(self):
  rng=np.random.RandomState(111);p=rng.normal(0,.2,(60,3));g=np.zeros(60,int)
  with self.assertRaises(ValueError):select_foreground(p,g,np.linalg.norm(p[:,:2],axis=1),np.ones(60,bool))
if __name__=='__main__':unittest.main()
