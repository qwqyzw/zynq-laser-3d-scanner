import unittest
from unittest.mock import patch
from pathlib import Path
import cv2,numpy as np
import switchable_pl_camera as camera

class Checks(unittest.TestCase):
 def test_actual_tie_frame_and_tampered_output(self):
  root=Path('/home/xilinx/jupyter_notebooks/button_scan/runs/999a15e7acaa406db5e4489e5742af59/scan/point_003/exp_20/images')
  if not root.exists():self.skipTest('Real regression fixture is on Zynq')
  image=cv2.imread(str(root/'raw_00.png'));words=np.fromfile(root/'pl_00_actual.bin',dtype='<u4')
  stripe,report=camera.checked_same_image_reference(image,(175,210,440,340),words)
  self.assertTrue(report['integer_reference_bit_exact']);self.assertEqual([v['x_px'] for v in report['float_equal_peak_ties']],[379])
  wrong=words.copy();wrong[379]^=np.uint32(1)
  with self.assertRaisesRegex(RuntimeError,'actual PL words'):camera.checked_same_image_reference(image,(175,210,440,340),wrong)
 def test_non_tie_float_disagreement_still_rejected(self):
  image=np.zeros((480,640,3),np.uint8);image[250:253,200:240,2]=100
  words=camera.integer_reference.extract_integer(image,(175,210,440,340),3072,0)
  real=camera.extract_horizontal(image,(175,210,440,340),12,0);self.assertTrue(real['valid'][210])
  real['y'][210]+=10
  with patch.object(camera,'extract_horizontal',return_value=real),self.assertRaisesRegex(RuntimeError,'non-tie'):
   camera.checked_same_image_reference(image,(175,210,440,340),words)
 def test_validity_disagreement_still_rejected(self):
  image=np.zeros((480,640,3),np.uint8);words=np.zeros(640,np.uint32)
  real=camera.extract_horizontal(image,(175,210,440,340),12,0);real['valid'][210]=True
  with patch.object(camera,'extract_horizontal',return_value=real),self.assertRaisesRegex(RuntimeError,'validity'):
   camera.checked_same_image_reference(image,(175,210,440,340),words)
if __name__=='__main__':unittest.main(verbosity=2)
