"""Managed BTN0 dual exposure, measured temporal support, audited BTN1.

Parent owns GPIO/service lock/publication. Child owns hardware lock/DMA/camera
and is never killed. Frozen73 reviews measured output before any surface job.
"""
from pathlib import Path
import argparse,json,os,sys,time,uuid
HERE=Path(__file__).resolve().parent;P=HERE.parent
sys.path.insert(0,str(P/'73_pointcloud_review_first_20261024'))
import review_first_service as base
from release85 import verify_release,sha
from button_audit import AuditedGate
original=base.original

class Coordinator(base.Coordinator):
 def __init__(self,*args,dual_manifest_sha256,**kwargs):
  self.dual_manifest_sha256=dual_manifest_sha256;self.last_progress=0;self.audit_error=None
  super().__init__(*args,**kwargs);self.gate=AuditedGate(self.gate,self.audit)
 def audit(self,event):
  entry={'session_id':self.session_id,'run_id':self.run_id,'stage':self.stage,'state':self.state,
         'process_id':os.getpid(),'child_pid':self.process.pid if self.process else None,**event}
  try:
   path=self.root/'button_events.jsonl'
   if path.exists() and path.stat().st_size>2*1024*1024:path.replace(self.root/'button_events.previous.jsonl')
   with path.open('a',encoding='utf-8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
  except OSError as e:self.audit_error=str(e)  # Never inhibit bounded stop for a logging error.
 def status(self,state,**extra):
  extra.update(acquisition_profile='dual_10degree_temporal_consensus',exposures=[5,20],angular_step_deg=10,
               surface_construction='deferred_pending_pointcloud_and_shape_review')
  if self.audit_error:extra['button_audit_error']=self.audit_error
  return super().status(state,**extra)
 def start(self):
  if self.process is not None or self.exiting:return
  accepted=time.monotonic();self.status('starting',message='BTN0 accepted; verifying dual-exposure scan')
  verify_release(self.dual_manifest_sha256);self.verified_current()
  if self.exiting or self.gpio is not None and int(self.gpio.read(0))&2:
   self.audit({'kind':'start_blocked_by_stop','monotonic_s':time.monotonic()});self.status('ready');return
  self.run_id=uuid.uuid4().hex;self.run=self.root/'runs'/self.run_id;self.run.mkdir(parents=True,exist_ok=False)
  self.cancelled=False
  original.save(self.run/'dual_binding.json',{'manifest_sha256':self.dual_manifest_sha256,'session_id':self.session_id,
       'angular_step_deg':10,'exposures':[5,20],'parent_owns_GPIO':True,'child_never_killed':True,
       'two_frame_support_enabled':True,'original_common10_gate_preserved':True,'surface_fitted':False})
  args=[sys.executable,'-B','-u',str(HERE/'optimized_dual_scan.py'),'--run-root',str(self.run),
        '--session-id',self.session_id,'--manifest-sha256',self.dual_manifest_sha256,'--client-date',self.config['client_date']]
  original.Coordinator.launch(self,args,'acquiring')
  original.save(self.run/'startup_timing.json',{'button_stable_press_monotonic_s':self.button_pressed_monotonic,
      'button_accepted_monotonic_s':accepted,'child_launched_monotonic_s':time.monotonic(),
      'camera_started_by_parent':False,'mode':'dual10_consensus'})
  self.audit({'kind':'child_launched','monotonic_s':time.monotonic()})
 def request_stop(self):
  action=self.gate.last_action;self.gate.last_action=None
  source='debounced_gpio_BTN1' if action and action['action']=='stop' and time.monotonic()-action['monotonic_s']<.25 else 'service_signal_or_programmatic'
  self.audit({'kind':'stop_requested','source':source,'monotonic_s':time.monotonic(),
              'gpio_action':action if source=='debounced_gpio_BTN1' else None})
  if self.exiting and self.process is None and source!='debounced_gpio_BTN1':
   self.audit({'kind':'maintenance_idle_exit_preserves_completed_result','monotonic_s':time.monotonic()})
   return
  return super().request_stop()
 def _assert_discard_quiescent(self,path):
  trial=Path(path)/'trial.json'
  if trial.is_file() and original.load(trial).get('schema')=='paired_actual_PL_trial_v2':
   if self.process is not None:raise RuntimeError('Managed child still owns hardware')
   verify_release(self.dual_manifest_sha256)
   from cleanup85 import inspect_cleanup
   data=original.load(trial)
   if data.get('version')=='20261024.core_stripe_maintenance.v1':
    import importlib.util
    sys.path.append(str(P/'81_core_maintenance_fix_20261024'))
    spec=importlib.util.spec_from_file_location('old_cleanup81',P/'81_core_maintenance_fix_20261024/cleanup81.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    result=m.inspect_cleanup(path,'a2520df9d5183c2450689c2573d5961ce742d2d6c12fe52a31da471bad349de9')
   elif data.get('version')=='20261024.consensus_dual_button.v1':
    import importlib.util
    sys.path.append(str(P/'74_consensus_dual_button_20261024'))
    spec=importlib.util.spec_from_file_location('old_cleanup74',P/'74_consensus_dual_button_20261024/cleanup74.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    result=m.inspect_cleanup(path,'6190ee6847fe32d0ecbd338c8463ee3e31bdd13eee6a03a55e16506b1b37028c')
   else:result=inspect_cleanup(path,self.dual_manifest_sha256)
   import fcntl,subprocess
   with open('/tmp/laser_motor_object_scan.lock','a') as owner:
    fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if subprocess.run(['fuser','/dev/video0'],capture_output=True,timeout=5).returncode!=1:raise RuntimeError('Camera remains occupied')
   return result
  return super()._assert_discard_quiescent(path)
 def poll(self):
  if self.process is None:return
  path=self.run/'trial.json'
  if self.process.poll() is None:
   if self.cancelled and (self.run/'scan').is_dir():(self.run/'scan/abort.request').touch()
   now=time.monotonic()
   if now-self.last_progress>.5:
    self.last_progress=now
    try:
     report=original.load(path);poses=report.get('poses',[])
     if report.get('manual_owner_hold') or report.get('recovery_hold'):
      self.status('recovery_required',message='Hardware owner retained; no automatic kill')
     elif not self.cancelled:
      self.status('acquiring',completed_poses=sum(p.get('status')=='completed' for p in poses),total_poses=37,
           current_angle_deg=poses[-1]['nominal_deg'] if poses else None,
           elapsed_seconds=report.get('elapsed_s'),worker_state=report['status'])
    except (OSError,ValueError):pass
   return
  code=self.process.returncode
  if self.process.stdin:self.process.stdin.close()
  self.process=None;self.log.close();self.log=None
  report=original.load(path) if path.is_file() else {}
  if report.get('status')=='cancelled':self.cancelled=True
  if self.cancelled:return self.finish('failed','User stop requested')
  if code!=0 or report.get('status')!='ready_diagnostic_preview':
   return self.finish('failed',report.get('error','Dual worker failed; inspect acquiring.log'))
  from cleanup85 import inspect_cleanup
  inspect_cleanup(self.run,self.dual_manifest_sha256)
  self.audit({'kind':'capture_completed','monotonic_s':time.monotonic(),'points':report['summary']['points']})
  return self.finish('ready_diagnostic_preview')
 def finish(self,state,error=None):
  if not self.cancelled and error is None:
   verify_release(self.dual_manifest_sha256)
   self.stage='reconstructing_on_zynq'  # Frozen73 review path; no surface child.
  return super().finish(state,error)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
 parser.add_argument('--dual-manifest-sha256',required=True);parser.add_argument('--load-board',action='store_true');parser.add_argument('--serve-board',action='store_true')
 args=parser.parse_args();data=verify_release(args.dual_manifest_sha256)
 old,identity,binding=base.verify(data['base73_manifest_sha256']);cfg,art=base.base.base.base.legacy.inspect_config(args.config);cfg['config_file']=args.config.resolve()
 if args.load_board:
  from load_button_overlay import load_board
  print(json.dumps(load_board(cfg,art)),flush=True)
 if args.serve_board:Coordinator(cfg,art,dual_manifest_sha256=args.dual_manifest_sha256,
      review_manifest_sha256=data['base73_manifest_sha256'],visual_manifest_sha256=old['base71_manifest_sha256'],
      compat_manifest_sha256=binding['base68_manifest_sha256'],startup_manifest_sha256=identity['base66_manifest_sha256']).serve()
 elif not args.load_board:print(json.dumps({'hardware_accessed':False,'profile':'dual10_consensus'}))
if __name__=='__main__':main()
