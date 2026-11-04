from pathlib import Path
import sys,json,subprocess,time,hashlib
P=Path('/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020');D=P/'90_export_lifecycle_fix_20261024';B=Path('/home/xilinx/jupyter_notebooks/button_scan')
sys.path[:0]=[str(D),str(P/'88_demo_result_recovery_20261024'),str(P/'86_integer_reference_fix_20261024')]
from demo_recovery import load,sha,save,PIN86
from cleanup86 import inspect_cleanup
status=load(B/'status.json');rid=status.get('run_id');assert rid=='0de78f1c77684877972e84d445880b69' and status.get('child_pid') is None and status.get('state')=='failed'
root=B/'runs'/rid;inspect_cleanup(root,PIN86);trial_hash=sha(root/'trial.json')
r=subprocess.run([sys.executable,'-B','-m','unittest','test_failure_lifecycle','-v'],cwd=D,capture_output=True,text=True,timeout=20);print(r.stdout+r.stderr);assert r.returncode==0
unit='[Service]\nExecStart=\nExecStart='+sys.executable+' -B -u '+str(D/'lifecycle_service.py')+' --lifecycle-manifest-sha256 '+sha(D/'manifest.json')+' --demo-manifest-sha256 '+sha(P/'88_demo_result_recovery_20261024/manifest.json')+' --config '+str(B/'config_current_axis_20261024.json')+' --dual-manifest-sha256 '+PIN86+' --load-board --serve-board\n'
(D/'service_before90.txt').write_text(subprocess.check_output(['systemctl','cat','zynq-button-scan.service'],text=True))
subprocess.run(['systemctl','stop','zynq-button-scan.service'],check=True,timeout=25)
drop=Path('/etc/systemd/system/zynq-button-scan.service.d/90-lifecycle.conf');assert not drop.exists();drop.write_text(unit);(D/'90-lifecycle.conf').write_text(unit)
try:
 subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15);subprocess.run(['systemctl','start','zynq-button-scan.service'],check=True,timeout=15)
 for _ in range(30):
  time.sleep(1);now=load(B/'status.json')
  if now.get('process_id')!=status['process_id'] and now.get('run_id')==rid and now.get('failure_code')=='fixed_scene_reference_failed':break
 else:raise RuntimeError('Lifecycle coordinator did not report the true failure')
 assert sha(root/'trial.json')==trial_hash
 result={'status':'fixed_failure_state_ready_for_manual_retry','board_status':now,'trial_unchanged':True,'pose_count':0,'new_scan_started':False,'new_motion_sent':False,'deployment_manifest_sha256':sha(D/'manifest.json')};save(D/'deployment.json',result);print(json.dumps(result,ensure_ascii=False))
except BaseException:
 subprocess.run(['systemctl','stop','zynq-button-scan.service'],timeout=25);drop.unlink(missing_ok=True);subprocess.run(['systemctl','daemon-reload'],check=True);subprocess.run(['systemctl','start','zynq-button-scan.service'],check=True);raise
