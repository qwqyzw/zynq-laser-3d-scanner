"""Normalize empty worker errors before inherited finish treats them as success."""
from pathlib import Path
import sys,json,hashlib
HERE=Path(__file__).resolve().parent;P=HERE.parent
sys.path[:0]=[str(HERE),str(P/'88_demo_result_recovery_20261024'),str(P/'86_integer_reference_fix_20261024')]
import demo_button_service as predecessor
from demo_recovery import load,save,sha
from failure_reason import classify
class Coordinator(predecessor.Coordinator):
 def finish(self,state,error=None):
  if state=='failed' and not self.cancelled and self.run is not None and (error is None or not str(error).strip()):
   try:trial=load(self.run/'trial.json')
   except (OSError,ValueError):trial={}
   error=classify(trial)['message']
  return super().finish(state,error)
 def attach(self):
  super().attach()
  previous=load(self.root/'latest_result.json')
  if previous.get('state')!='failed' or previous.get('discarded'):return
  rid=previous.get('run_id','')
  import re
  if not re.fullmatch('[0-9a-f]{32}',rid):return
  root=self.root/'runs'/rid
  if not (root/'trial.json').exists():return
  trial=load(root/'trial.json')
  if trial.get('status')!='failed':return
  from cleanup86 import inspect_cleanup
  inspect_cleanup(root,'06e6c6cc0167c16832edf85a0470499f7744062aeca473cf8e80f015a88d60b5')
  self.run,self.run_id=root,rid;reason=classify(trial)
  receipt={'source_trial_sha256':sha(root/'trial.json'),'old_published_error':previous.get('error'),'original_trial_unchanged':True,**reason}
  if not (root/'result_before90.json').exists():save(root/'result_before90.json',previous)
  save(root/'diagnostic90.json',receipt)
  previous.update(error=reason['message'],failure_code=reason['code'],diagnostic_receipt_sha256=sha(root/'diagnostic90.json'))
  save(root/'result.json',previous);save(self.root/'latest_result.json',previous)
  self.status('failed',error=reason['message'],completed_poses=reason['completed_poses'],total_poses=37,failure_code=reason['code'])
def main():
 key='--lifecycle-manifest-sha256';i=sys.argv.index(key);pin=sys.argv[i+1];del sys.argv[i:i+2]
 if sha(HERE/'manifest.json')!=pin:raise ValueError('Lifecycle manifest changed')
 for name,digest in load(HERE/'manifest.json')['files'].items():
  if Path(name).name!=name or sha(HERE/name)!=digest:raise ValueError('Lifecycle source changed')
 predecessor.Coordinator=Coordinator;predecessor.main()
if __name__=='__main__':main()
