import unittest,tempfile,json,time
from pathlib import Path
from unittest.mock import patch,MagicMock
import dual_button_service as service
from button_audit import AuditedGate

class Checks(unittest.TestCase):
 def coordinator(self,root):
  cfg=root/'config.json';cfg.write_text('{}')
  return service.Coordinator({'config_file':cfg,'output_root':root,'mode':'diagnostic_preview','client_date':'2026-10-24','project':root},
    {},dual_manifest_sha256='74fixture',review_manifest_sha256='73fixture',visual_manifest_sha256='71fixture',compat_manifest_sha256='68fixture',startup_manifest_sha256='66fixture')
 def test_managed_start_has_one_child_and_no_service_restart(self):
  with tempfile.TemporaryDirectory() as t:
   c=self.coordinator(Path(t))
   with patch.object(service,'verify_release'),patch.object(c,'verified_current'),patch.object(service.original.subprocess,'Popen') as p:
    p.return_value.pid=123;c.start();args=p.call_args.args[0]
    self.assertIn('--run-root',args);self.assertIn('--session-id',args);self.assertIn(c.session_id,args)
    self.assertEqual(c.stage,'acquiring');self.assertEqual(p.call_count,1);self.assertNotIn('systemctl',args)
   c.log.close()
 def test_cancel_never_terminates_hardware_owner(self):
  with tempfile.TemporaryDirectory() as t:
   c=self.coordinator(Path(t));c.run_id='a'*32;c.run=Path(t)/'runs'/c.run_id;(c.run/'scan').mkdir(parents=True)
   c.process=MagicMock();c.process.pid=12;c.process.poll.return_value=None;c.stage='acquiring'
   c.gate.last_action={'action':'stop','monotonic_s':time.monotonic(),'raw_bits':2}
   c.request_stop();self.assertTrue((c.run/'scan/abort.request').exists());c.process.terminate.assert_not_called();c.process.kill.assert_not_called()
   events=[json.loads(v) for v in (Path(t)/'button_events.jsonl').read_text().splitlines()]
   self.assertEqual(events[-1]['source'],'debounced_gpio_BTN1')
 def test_success_reviews_without_surface_and_keeps_raw_result(self):
  with tempfile.TemporaryDirectory() as t:
   c=self.coordinator(Path(t));c.run_id='a'*32;c.run=Path(t)/'runs'/c.run_id;c.stage='acquiring'
   viewer=c.run/'model/viewer';viewer.mkdir(parents=True)
   (viewer/'export_manifest.json').write_text(json.dumps({'coordinate_producer':'zynq','test_only':False,'point_statistics':{'finite_points':30}}))
   (viewer/'source_meta.json').write_text(json.dumps({'diagnostic_preview_only':True,'alignment_validated':False,'metric_accuracy_accepted':False}))
   review=c.run/'model/pointcloud_review';review.mkdir();(review/'review_manifest.json').write_text('{}')
   with patch.object(service,'verify_release'),patch.object(service.base,'verify'),patch.object(service.base,'review',return_value={'empty_unique_angles_deg':[]}),patch.object(c,'launch') as launch:
    c.finish('ready_diagnostic_preview')
   launch.assert_not_called();result=json.loads((Path(t)/'latest_result.json').read_text());self.assertNotIn('surface_relative',result);self.assertTrue(result['surface_review_required'])
 def test_failure_does_not_claim_completed_cloud(self):
  with tempfile.TemporaryDirectory() as t:
   c=self.coordinator(Path(t));c.run_id='a'*32;c.run=Path(t)/'runs'/c.run_id;c.run.mkdir(parents=True);c.stage='acquiring'
   c.process=MagicMock();c.process.poll.return_value=1;c.process.returncode=1;c.log=MagicMock()
   c.poll();result=json.loads((Path(t)/'latest_result.json').read_text())
   self.assertEqual(result['state'],'failed');self.assertNotIn('viewer_relative',result)
 def test_gate_audit_does_not_change_debounce_or_repeat_cancel(self):
  from cancel_discard import cancel_gate
  entries=[];gate=AuditedGate(cancel_gate(service.original.ButtonGate).__class__(),entries.append)
  self.assertIsNone(gate.update(0,0,False));self.assertIsNone(gate.update(0,.1,False))
  self.assertIsNone(gate.update(2,.2,True));self.assertEqual(gate.update(2,.3,True),'stop');self.assertIsNone(gate.update(2,1.,True))
  self.assertEqual(sum(e['action']=='stop' for e in entries),1)
 def test_gpio_not_high_does_not_create_cancel(self):
  entries=[];gate=AuditedGate(service.original.ButtonGate(),entries.append)
  for i in range(100):self.assertIsNone(gate.update(0,i*.02,False))
  self.assertFalse(any(e['action']=='stop' for e in entries))

if __name__=='__main__':unittest.main(verbosity=2)
