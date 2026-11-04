"""Threshold boundaries and archived failed PL batch through all production paths."""
from pathlib import Path
import ast,csv,hashlib,json,shutil,sys,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
R=Path(__file__).resolve().parent;P=R.parent
sys.path.insert(0,str(R));sys.path.insert(0,str(P/'04_runtime'));sys.path.insert(0,str(P/'09_tools'))
import quality_columns10 as quality
import quality_from_stripes_fast as original


class Checks(unittest.TestCase):
 def test_only_requested_quality_threshold_changed(self):
  old=(P/'04_runtime/quality_from_stripes_fast.py').read_text(encoding='utf-8')
  new=(R/'quality_columns10.py').read_text(encoding='utf-8')
  new=new.replace('minimum_common_columns=10','minimum_common_columns=20').replace('minimum_common_columns<10','minimum_common_columns<20').replace('at least 10 columns','at least 20 columns')
  def without_doc(s):
   tree=ast.parse(s);tree.body=tree.body[1:];return ast.dump(tree,include_attributes=False)
  self.assertEqual(without_doc(old),without_doc(new))

 def test_real_xyz_and_motor_logic_unchanged(self):
  def functions(f):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(f.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef)}
  old=P/'45_integer_quality_release_20261022'
  a,b=functions(old/'fast_reconstruct_on_zynq.py'),functions(R/'fast_reconstruct_on_zynq.py')
  for name in a:
   if name!='modules':self.assertEqual(a[name],b[name],name)
  self.assertEqual((old/'object_board_scan_360.py').read_text(),(R/'object_board_scan_360.py').read_text())
  self.assertEqual((old/'fullturn_closure.py').read_text(),(R/'fullturn_closure.py').read_text())

 def test_9_rejected_10_19_20_accepted_without_changing_points(self):
  roi=(175,210,440,340)
  for count in [0,9,10,19,20]:
   image=np.zeros((480,640,3),np.uint8);image[280,200:200+count,2]=100
   valid=np.zeros(640,bool);valid[200:200+count]=True
   stripe={'valid':valid,'y':np.where(valid,280.,np.nan)}
   new,nq=quality.review_from_stripes_fast([image]*3,[stripe]*3,roi)
   old,oq=original.review_from_stripes_fast([image]*3,[stripe]*3,roi)
   self.assertTrue(np.array_equal(new,old))
   self.assertEqual(nq['common_quality_columns'],count)
   self.assertEqual(nq['image_quality_gate_passed'],count>=10)
   for k in nq:
    if k not in ['minimum_common_columns','image_quality_gate_passed']:self.assertEqual(nq[k],oq[k])
  with self.assertRaises(ValueError):quality.review_from_stripes_fast([image]*3,[stripe]*3,roi,9)

 def test_new_profile_and_import_routes(self):
  from fast_release import QUALITY,EXPECTED_PROFILE,verify_release,digest
  self.assertEqual(QUALITY['minimum_common_columns'],10)
  self.assertEqual(EXPECTED_PROFILE['quality']['minimum_common_columns'],10)
  verify_release(P,R,digest(R/'SHA256.json'))
  for name in ['persistent_pl_camera.py','single_pl_review.py']:
   self.assertIn('from quality_columns10 import review_from_stripes_fast',(R/name).read_text())
  self.assertIn("result['quality_from_stripes_fast'] = quality",(R/'fast_reconstruct_on_zynq.py').read_text())

 def test_failed_170_degree_batch_through_adapter_and_reconstruction(self):
  try:import cv2
  except ImportError:self.skipTest('cv2 only available on Zynq; full replay runs there')
  source=Path('/home/xilinx/jupyter_notebooks/button_scan/runs/732f8e526a534700820727cb2deecd4f/scan/point_034/images')
  if not source.exists():self.skipTest('Archived board batch unavailable locally')
  from persistent_pl_camera import PersistentPLCamera,PersistentCamera,StripeConfig
  from single_pl_review import review_batch
  import fast_reconstruct_on_zynq as recon
  def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
  originals={v.name:sha(v) for v in source.iterdir() if v.is_file()}
  records=json.loads((source/'pl_transactions.json').read_text())
  class ReplayDriver:
   healthy=True
   def __init__(self):self.calls=0
   def run_words(self,packed,config):
    i=self.calls;self.calls+=1
    words=np.fromfile(source/('pl_%02d_actual.bin'%i),dtype='<u4');valid=words>>31!=0
    return SimpleNamespace(words=words,valid=valid,y=np.where(valid,(words&0x3ffff)/256,np.nan),
                           cycles=records[i]['cycles'],completed_frames=records[i]['completed_frames'])
  with tempfile.TemporaryDirectory(prefix='quality10_actual_PL_replay_') as tmp:
   folder=Path(tmp)
   for name in ['capture.json']+['raw_%02d.png'%i for i in range(3)]:shutil.copyfile(source/name,folder/name)
   camera=PersistentPLCamera.__new__(PersistentPLCamera)
   camera.roi=(175,210,440,340);camera.verify_ps=True;camera.driver=ReplayDriver();camera.config=StripeConfig(*camera.roi,threshold_q8=3072,min_run=8)
   camera.capture_blocked=False
   batch={'images':[{'file':str(folder/('raw_%02d.png'%i))} for i in range(3)]}
   with patch.object(PersistentCamera,'capture',return_value=batch):result=camera.capture(folder)
   self.assertEqual(camera.driver.calls,3);self.assertFalse(camera.capture_blocked)
   self.assertEqual(result['stripe_quality']['minimum_common_columns'],10)
   self.assertEqual(result['stripe_quality']['common_quality_columns'],19)
   self.assertTrue(result['stripe_quality']['image_quality_gate_passed'])
   review=review_batch(folder,camera.roi,5)
   self.assertEqual(review['common_quality_columns'],19)
   pixels=recon.actual_pl_pixels(folder,camera.roi,SimpleNamespace(quality_from_stripes_fast=quality))
   self.assertEqual(len(pixels),19)
   old=json.loads((source/'stripe_quality.json').read_text());new=result['stripe_quality']
   self.assertFalse(old['image_quality_gate_passed'])
   for k in old:
    if k not in ['minimum_common_columns','image_quality_gate_passed']:self.assertEqual(old[k],new[k])
  self.assertEqual(originals,{v.name:sha(v) for v in source.iterdir() if v.is_file()})


if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
 print(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
                  'all_passed':result.wasSuccessful(),'new_PL_transactions':0,'motor_commands_sent':False,'real_XYZ_computed':False}))
 raise SystemExit(0 if result.wasSuccessful() else 1)
