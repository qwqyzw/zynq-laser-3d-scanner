from pathlib import Path
import sys,json,re
HERE=Path(__file__).resolve().parent;P=HERE.parent
sys.path[:0]=[str(HERE),str(P/'90_export_lifecycle_fix_20261024')]
import dual_button_service as capture
from demo_recovery91 import prepare,load,save,sha,ERROR,PIN86
from compat_cleanup import inspect_cleanup
from failure_reason import classify
from scene_limits import limits
class Coordinator(capture.Coordinator):
 def __init__(self,*args,**kwargs):self.demo_receipt=None;super().__init__(*args,**kwargs)
 def start(self):self.demo_receipt=None;return super().start()
 def status(self,state,**extra):
  extra['scene_demo_limits']=limits();extra['active_calibration_release']='100_relocation_geometry_20261102';extra['active_geometry_sha256']='6a14cb739ccf39162d561721dc9f73143d3f09d5004b200f1b64b7251db527c5'
  return super().status(state,**extra)
 def _assert_discard_quiescent(self,path):
  data=load(Path(path)/'trial.json') if (Path(path)/'trial.json').exists() else {}
  if data.get('release_manifest_sha256')!=self.dual_manifest_sha256:
   if self.process is not None:raise RuntimeError('Child still owns hardware')
   checked=inspect_cleanup(path,PIN86)
   import fcntl,subprocess
   with open('/tmp/laser_motor_object_scan.lock','a') as owner:
    fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if subprocess.run(['fuser','/dev/video0'],capture_output=True,timeout=5).returncode!=1:raise RuntimeError('Camera remains occupied')
   return checked
  return super()._assert_discard_quiescent(path)
 def finish(self,state,error=None):
  if state=='failed' and not self.cancelled and self.run is not None:
   if not error:error=classify(load(self.run/'trial.json'))['message']
   if error==ERROR:
    # Publish the original failure identity before the bound display-only recovery.
    super().finish('failed',error)
    try:self.demo_receipt=prepare(self.run)
    except Exception as exc:return super().finish('failed',error+'; 演示预览不满足条件：'+str(exc))
    state='ready_diagnostic_preview';error=None
  result=super().finish(state,error)
  if self.run is not None and not self.cancelled and error is None:
   record=load(self.root/'latest_result.json')
   if record.get('run_id')==self.run_id and record.get('state')=='ready':
    note='演示模式：背景参考容差为4像素、相似度0.75；几何精度未验收。'
    if self.demo_receipt:note+=' '+self.demo_receipt['demo_policy']['quality_note']
    trial=load(self.run/'trial.json');closure=trial.get('closure',{});note+=' '+str(closure.get('preview_warning') or '');record.update(demonstration_only=True,demo_quality_note=note,scene_demo_limits=limits(),endpoint_closure=closure,strict_closure_passed=bool(closure.get('passed')))
    save(self.run/'result.json',record);save(self.root/'latest_result.json',record)
  return result
 def attach(self):
  super().attach();record=load(self.root/'latest_result.json');rid=record.get('run_id','')
  if not re.fullmatch('[0-9a-f]{32}',rid) or record.get('discarded'):return
  root=self.root/'runs'/rid
  if not (root/'trial.json').exists():return
  trial=load(root/'trial.json')
  if record.get('state')=='failed' and trial.get('release_manifest_sha256')!=self.dual_manifest_sha256:
   # A failed historical result is preserved, never adopted or discarded.
   # The new release must independently verify LIVE quiescence under its lock.
   import fcntl,subprocess
   import object_board_scan_360 as acq
   with open('/tmp/laser_motor_object_scan.lock','a') as owner:
    fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if subprocess.run(['fuser','/dev/video0'],capture_output=True,timeout=5).returncode!=1:raise RuntimeError('Live camera is occupied')
    runtime=acq.attach_loaded_runtime(P,acq.load_modules(P,board=True),self.artifacts)
    diagnostics=runtime.driver.diagnostics();acq.assert_idle(diagnostics)
    if not runtime.driver.healthy or runtime.driver.quarantined_buffer_count:raise RuntimeError('Live PL driver is not healthy')
   save(HERE/'activation_history_preserved.json',{'historical_run_id':rid,'historical_trial_sha256':sha(root/'trial.json'),'historical_failed_record_preserved':True,'old_cleanup_not_accepted':True,'historical_result_not_adopted':True,'historical_data_not_discarded':True,'live_diagnostics':diagnostics,'new_release_manifest_sha256':self.dual_manifest_sha256})
   self.run,self.run_id=None,None
   self.status('waiting_for_release',message='新标定就绪；旧失败轮次保留，等待松开所有按键。')
   return
  try:inspect_cleanup(root,PIN86)
  except RuntimeError as exc:
   trial=load(root/'trial.json');boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
   if trial.get('boot_id')!=boot and record.get('state')=='failed':
    save(HERE/'prior_boot_unfinished.json',{'run_id':rid,'prior_boot_id':trial.get('boot_id'),'current_boot_id':boot,'record_preserved':True,'result_adopted':False,'reason':str(exc)})
    return
   raise
  self.run,self.run_id=root,rid
  if record.get('state')=='ready':self.status('ready_diagnostic_preview')
  else:self.status('ready',previous_run_error=record.get('error','此前扫描未完成'),message='当前几何已重新标定，等待BTN0；上一轮失败记录保留。')
def main():
 capture.Coordinator=Coordinator;capture.main()
if __name__=='__main__':main()
