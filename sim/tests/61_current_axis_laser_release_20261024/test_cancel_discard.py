"""Cancel/discard regression tests; no camera, UART, DMA, GPIO or real motion."""
from pathlib import Path
import json,sys,tempfile,unittest,os
from types import SimpleNamespace
from unittest.mock import patch
R=Path(__file__).resolve().parent;P=R.parent
sys.path.insert(0,str(R));sys.path.insert(0,str(P/'09_tools'))
import fast_button_service as service
from cancel_discard import cancel_gate
from board_button_scan_service import ButtonGate

class FakeChild:
 pid=123
 def __init__(self):self.terminated=False
 def poll(self):return None
 def terminate(self):self.terminated=True

class Checks(unittest.TestCase):
 def controller(self,folder):
  cfg=folder/'config.json';cfg.write_text('{}')
  config={'config_file':cfg,'output_root':folder,'mode':'diagnostic_preview','client_date':'2026-10-22',
          'project':P,'overlay_release':P/'17_button_scan_release_20261020','roi':[175,210,440,340],
          'exposure':5,'max_angle':360,'fast_release':R}
  c=service.Coordinator(config,{'button_release':{'manifest_sha256':'SIMULATED'}})
  c.run_id='a'*32;c.run=folder/'runs'/c.run_id;(c.run/'scan').mkdir(parents=True)
  (c.run/'measured_data.csv').write_text('SIMULATED_CANCEL_FIXTURE')
  c._assert_discard_quiescent=lambda path:None
  return c

 def test_acquisition_stop_is_bounded_then_data_discarded(self):
  with tempfile.TemporaryDirectory() as tmp:
   c=self.controller(Path(tmp));c.process=FakeChild();c.stage='acquiring';old=c.run
   c.request_stop()
   self.assertTrue((old/'scan/abort.request').is_file());self.assertFalse(c.process.terminated)
   self.assertTrue(old.exists());c.process=None;c.finish('failed','User stop requested')
   self.assertFalse(old.exists());self.assertIsNone(c.run_id);self.assertEqual(c.state,'ready')
   record=json.loads((Path(tmp)/'latest_result.json').read_text())
   self.assertTrue(record['discarded']);self.assertNotIn('viewer_relative',record)

 def test_reconstruction_stop_terminates_only_math_child(self):
  with tempfile.TemporaryDirectory() as tmp:
   c=self.controller(Path(tmp));c.stage='reconstructing_on_zynq';c.process=FakeChild()
   c.request_stop();self.assertTrue(c.process.terminated);self.assertTrue(c.run.exists())

 def test_retained_resources_prevent_deletion(self):
  with tempfile.TemporaryDirectory() as tmp:
   c=self.controller(Path(tmp));old=c.run
   def uncertain(path):raise RuntimeError('camera retained')
   c._assert_discard_quiescent=uncertain;c.cancelled=True;c.finish('failed','User stop requested')
   self.assertTrue(old.exists());self.assertIn('Discard pending',json.loads((Path(tmp)/'latest_result.json').read_text())['error'])

 def test_scope_never_deletes_other_runs_or_geometry(self):
  with tempfile.TemporaryDirectory() as tmp:
   c=self.controller(Path(tmp));other=Path(tmp)/'runs'/('b'*32);other.mkdir()
   geometry=Path(tmp)/'geometry.json';geometry.write_text('KEEP_GEOMETRY')
   c.request_stop();self.assertTrue(other.exists());self.assertEqual(geometry.read_text(),'KEEP_GEOMETRY')

 def test_outside_path_refused(self):
  with tempfile.TemporaryDirectory() as tmp:
   c=self.controller(Path(tmp));c.run=Path(tmp);c.cancelled=True
   with self.assertRaisesRegex(RuntimeError,'outside'):c.finish('failed')

 def test_idle_btn1_once_and_fresh_release_before_start(self):
  g=cancel_gate(ButtonGate)
  for t,b in [(0,0),(.1,0),(.2,2)]:self.assertIsNone(g.update(b,t,False))
  self.assertEqual(g.update(2,.31,False),'stop');self.assertIsNone(g.update(2,.5,False))
  self.assertIsNone(g.update(1,.6,False));self.assertIsNone(g.update(1,2,False))
  g.update(0,2.1,False);g.update(0,2.3,False);g.update(1,2.4,False);g.update(1,2.6,False)
  self.assertEqual(g.update(1,3.7,False),'start')

 def test_new_start_is_new_uuid_and_no_previous_points(self):
  with tempfile.TemporaryDirectory() as tmp:
   c=self.controller(Path(tmp));old_id=c.run_id;c.request_stop()
   with patch.object(c,'verified_current'),patch.object(service,'inspect_config',return_value=(c.config,c.artifacts)),patch.object(c,'launch'):
    c.start()
   self.assertNotEqual(c.run_id,old_id);self.assertTrue(c.run.is_dir())
   self.assertFalse((c.run/'measured_data.csv').exists())

 def test_threshold_and_acquisition_math_are_still_51(self):
  old=P/'51_quality10_release_20261022'
  for name in ['quality_columns10.py','persistent_pl_camera.py','single_pl_review.py','object_board_scan_360.py','fullturn_closure.py']:
   self.assertEqual((old/name).read_bytes(),(R/name).read_bytes(),name)

 def test_pc_discard_closes_owned_preview_and_removes_only_cancelled_run(self):
  from pc_discard import discard_local
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);identity='a'*32+'_'+'b'*32;output=root/'received';target=output/identity;target.mkdir(parents=True)
   (target/'points.csv').write_text('SIMULATED_DISCARD_FIXTURE');other=output/('c'*32+'_'+'d'*32);other.mkdir()
   appdata=root/'appdata';manifest='e'*64
   for name in ['cloudcompare_cache','display_receipts']:(appdata/'ZynqPointCloud'/name/manifest).mkdir(parents=True)
   receipts={identity:{'manifest_sha256':manifest}}
   with patch.dict(os.environ,{'LOCALAPPDATA':str(appdata)}),patch('pc_discard.close_owned_viewer',return_value=True) as close:
    result=discard_local(identity,receipts,output)
   self.assertEqual(result['owned_viewers_closed'],1);close.assert_called_once()
   self.assertFalse(target.exists());self.assertTrue(other.exists())
   self.assertFalse((appdata/'ZynqPointCloud/cloudcompare_cache'/manifest).exists())

 def test_pc_discard_refuses_outside_root(self):
  from pc_discard import remove_child
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);child=root/'safe';child.mkdir()
   with self.assertRaises(ValueError):remove_child(child,root)

if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
 print(json.dumps({'all_passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),
                  'errors':len(result.errors),'new_PL_transactions':0,'real_motor_commands':0}))
 raise SystemExit(0 if result.wasSuccessful() else 1)
