"""Validate either frozen61 or actual-PL65 cleanup; never synthesize receipts."""
from pathlib import Path
import hashlib,json,re,subprocess

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def inspect_cleanup(path):
    path=Path(path)
    old=path/'scan/completion.json'
    if old.is_file():
        data=json.loads(old.read_text());kind='legacy61_completion'
    else:
        record=path/'trial.json'
        if not record.is_file():raise RuntimeError('Neither supported cleanup receipt exists')
        data=json.loads(record.read_text());kind='actual_PL65_trial'
        if data.get('schema')!='paired_actual_PL_trial_v1' or data.get('version')!='20261024.dual_actual_PL.experimental.v1':
            raise RuntimeError('Unrecognized experimental cleanup schema')
        if data.get('run_id')!=path.name or not re.fullmatch('[0-9a-f]{32}',data.get('session_id','')):
            raise RuntimeError('Experimental cleanup run/session identity differs')
        if data.get('manual_owner_hold') or data.get('status') not in ('ready_diagnostic_preview','cancelled','failed'):
            raise RuntimeError('Experimental owner has not reached a final state')
        receipt=path/'scan/camera_session/session_close.json'
        camera=data.get('cleanup',{}).get('camera',{})
        if not receipt.is_file() or sha(receipt)!=camera.get('session_close_sha256'):
            raise RuntimeError('Camera final receipt hash differs')
        close=json.loads(receipt.read_text())
        if not (close.get('camera_release_confirmed') is True and close.get('exposure_restoration_verified') is True
                and close.get('restore_errors')==[] and close.get('error') is None and close.get('explicit_close_received') is True):
            raise RuntimeError('Physical camera restoration receipt rejected')
        worker=path/'scan/persistent_camera_worker.py'
        if not worker.is_file() or sha(worker)!=close.get('worker_sha256'):
            raise RuntimeError('Camera worker receipt binding differs')
        diag=data.get('final_diagnostics',{});dma=diag.get('dma',{})
        if (type(diag.get('status')) is not int or diag['status']&9 or
                not all(type(dma.get(k)) is int and dma[k]&2 and not dma[k]&0x770 for k in ('mm2s_sr','s2mm_sr'))):
            raise RuntimeError('Experimental final DMA idle evidence rejected')
    cleanup=data.get('cleanup',{});camera=cleanup.get('camera',{})
    if not (cleanup.get('motor_stop_confirmed') is True and camera.get('camera_release_confirmed') is True
            and camera.get('no_active_capture_process') is True and camera.get('exposure_restoration_verified') is True
            and data.get('driver_healthy') is True and data.get('quarantined_buffers')==0
            and not data.get('recovery_hold')):
        raise RuntimeError('Hardware cleanup remains unverified; keep data and owner')
    return {'kind':kind,'run_id':path.name,'record_validated':True}


class CompatibleCancelMixin:
    def _assert_discard_quiescent(self,path):
        if self.process is not None:raise RuntimeError('Child still owns scan')
        result=inspect_cleanup(path)
        import fcntl
        with open('/tmp/laser_motor_object_scan.lock','a') as owner:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if subprocess.run(['fuser','/dev/video0'],capture_output=True,timeout=5).returncode!=1:
                raise RuntimeError('Camera is occupied; keep data')
        return result
