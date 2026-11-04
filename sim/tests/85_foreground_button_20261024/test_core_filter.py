from pathlib import Path
import sys,unittest
P=Path(__file__).resolve().parent.parent;sys.path[:0]=[str(P/'04_runtime'),str(P/'01_fpga_baseline/reference')]
import numpy as np,roi_integer,integer_model
from laser_vertical_fast import keep_long_runs
from switchable_pl_camera import independent_integer_reference,PersistentPLCamera
class Core(unittest.TestCase):
 def test_core_coordinate_and_mask_equivalence(self):
  rng=np.random.RandomState(101);im=rng.randint(0,256,(480,640,3),dtype=np.uint8);roi=(175,210,440,340)
  core=roi_integer.extract_integer(im,roi,3072,0);np.testing.assert_array_equal(core,integer_model.extract_integer(im,roi,3072,0))
  for minimum in [4,8]:
   mask=keep_long_runs(core>>31!=0,minimum);derived=np.zeros(640,np.uint32);derived[mask]=core[mask]
   np.testing.assert_array_equal(derived,integer_model.extract_integer(im,roi,3072,minimum))
  stripe=independent_integer_reference(im,roi,core);np.testing.assert_array_equal(stripe['valid'],keep_long_runs(core>>31!=0,4))
 def test_supported_hardware_mode_selected(self):
  camera=PersistentPLCamera('pass',(175,210,440,340),None)
  self.assertEqual(camera.config.min_run,0)
if __name__=='__main__':unittest.main()
