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
  extra['scene_demo_limits']=limits()
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
    record.update(demonstration_only=True,demo_quality_note=note,scene_demo_limits=limits())
    save(self.run/'result.json',record);save(self.root/'latest_result.json',record)
  return result
 def attach(self):
  super().attach();record=load(self.root/'latest_result.json');rid=record.get('run_id','')
  if not re.fullmatch('[0-9a-f]{32}',rid) or record.get('discarded'):return
  root=self.root/'runs'/rid
  if not (root/'trial.json').exists():return
  try:inspect_cleanup(root,PIN86)
  except RuntimeError as exc:
   trial=load(root/'trial.json');boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
   if trial.get('boot_id')!=boot and record.get('state')=='failed':
    save(HERE/'prior_boot_unfinished.json',{'run_id':rid,'prior_boot_id':trial.get('boot_id'),'current_boot_id':boot,'record_preserved':True,'result_adopted':False,'reason':str(exc)})
    return
   raise
  self.run,self.run_id=root,rid
  if record.get('state')=='ready':self.status('ready_diagnostic_preview')
  else:self.status('failed',error=record.get('error','此前扫描未完成'),message='演示背景容差已启用，等待重新按BTN0。')
def main():
 capture.Coordinator=Coordinator;capture.main()
if __name__=='__main__':main()
