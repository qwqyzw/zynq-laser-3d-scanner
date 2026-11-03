"""Recognize only managed86 final receipts with bound source/camera evidence."""
from pathlib import Path
import json,re
from release91 import sha,verify_release

def inspect_cleanup(path,pin):
 binding=verify_release(pin);path=Path(path);data=json.loads((path/'trial.json').read_text())
 if data.get('schema')!='paired_actual_PL_trial_v2' or data.get('version')!='20261024.scene_demo.v1':raise RuntimeError('Unknown managed receipt')
 if data.get('run_id')!=path.name or not re.fullmatch('[0-9a-f]{32}',data.get('session_id','')):raise RuntimeError('Run/session mismatch')
 if data.get('release_manifest_sha256')!=pin or data.get('managed_child_complete') is not True:raise RuntimeError('Unfinished managed owner')
 if data.get('manual_owner_hold') or data.get('recovery_hold') or data.get('status') not in ['ready_diagnostic_preview','cancelled','failed']:raise RuntimeError('Nonfinal owner')
 required={'optimized_dual_scan.py','fuse_measured_profiles.py','diagnostic_camera.py','switchable_pl_camera.py','locked_switchable_camera_worker.py','temporal_consensus.py','roi_integer.py','foreground_select.py','scene_limits.py'}
 if set(data.get('source_sha256',{}))!=required:raise RuntimeError('Incomplete run source binding')
 for name,h in data.get('source_sha256',{}).items():
  if Path(name).name!=name or binding['files'].get(name)!=h or sha(path/'tested_sources'/name)!=h:raise RuntimeError('Run source differs')
 if not data.get('source_sha256'):raise RuntimeError('Missing run source binding')
 c=data.get('cleanup',{});camera=c.get('camera',{});receipt=path/'scan/camera_session/session_close.json'
 if sha(receipt)!=camera.get('session_close_sha256'):raise RuntimeError('Camera receipt hash differs')
 close=json.loads(receipt.read_text())
 if not (close.get('camera_release_confirmed') is True and close.get('exposure_restoration_verified') is True and close.get('restore_errors')==[] and close.get('error') is None and close.get('explicit_close_received') is True):raise RuntimeError('Camera restoration unverified')
 if sha(path/'scan/persistent_camera_worker.py')!=close.get('worker_sha256'):raise RuntimeError('Worker receipt binding differs')
 dma=data.get('final_diagnostics',{}).get('dma',{});status=data.get('final_diagnostics',{}).get('status')
 if type(status) is not int or status&9 or not all(type(dma.get(k)) is int and dma[k]&2 and not dma[k]&0x770 for k in ['mm2s_sr','s2mm_sr']):raise RuntimeError('DMA is not idle')
 if not (c.get('motor_stop_confirmed') is True and camera.get('camera_release_confirmed') is True and camera.get('no_active_capture_process') is True and camera.get('exposure_restoration_verified') is True and data.get('driver_healthy') is True and data.get('quarantined_buffers')==0):raise RuntimeError('Hardware cleanup incomplete')
 return {'kind':'managed86_trial','run_id':path.name,'record_validated':True}
