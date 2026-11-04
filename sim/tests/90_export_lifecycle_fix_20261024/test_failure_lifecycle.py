import unittest,tempfile,json,sys,types,importlib.util
from pathlib import Path
from failure_reason import classify
class Reasons(unittest.TestCase):
 def test_precheck_reports_zero_capture(self):
  r=classify({'poses':[],'error':'','fixed_before':{'all_passed':False,'patches':[{'shift_px':2.236,'correlation':.808}]}})
  self.assertEqual(r['code'],'fixed_scene_reference_failed');self.assertEqual(r['completed_poses'],0);self.assertIn('尚未采集',r['message']);self.assertFalse(r['export_expected'])
 def test_blank_assertion_has_nonempty_message(self):
  self.assertIn('AssertionError',classify({'error':'','traceback':'Traceback\nAssertionError'})['message'])
 def test_real_error_preserved(self):self.assertEqual(classify({'error':'UART timeout'})['message'],'UART timeout')
 def test_empty_error_never_takes_success_export_branch(self):
  class Base:
   def finish(self,state,error=None):
    if not error:raise AssertionError('Would incorrectly load missing export_manifest')
    return error
  pred=types.ModuleType('demo_button_service');pred.Coordinator=Base
  recovery=types.ModuleType('demo_recovery');recovery.load=lambda p:json.loads(Path(p).read_text());recovery.save=lambda *x:None;recovery.sha=lambda p:'unused'
  old={k:sys.modules.get(k) for k in ['demo_button_service','demo_recovery']}
  try:
   sys.modules.update(demo_button_service=pred,demo_recovery=recovery)
   spec=importlib.util.spec_from_file_location('tested_lifecycle',Path(__file__).with_name('lifecycle_service.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
   with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);(root/'trial.json').write_text(json.dumps({'error':'','poses':[],'fixed_before':{'all_passed':False,'patches':[]}}));obj=module.Coordinator();obj.run=root;obj.cancelled=False
    self.assertIn('固定背景',obj.finish('failed',''))
  finally:
   for k,v in old.items():
    if v is None:sys.modules.pop(k,None)
    else:sys.modules[k]=v
if __name__=='__main__':unittest.main()
