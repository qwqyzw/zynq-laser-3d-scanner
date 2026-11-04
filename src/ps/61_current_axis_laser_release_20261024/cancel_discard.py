"""User-authorized discard, after safe bounded stop and resource release."""
from pathlib import Path
import json,re,shutil,subprocess,uuid


def cancel_gate(base):
    class Gate(base):
        def __init__(self):
            super().__init__();self.idle_stop_sent=False
        def update(self,bits,now,busy):
            action=super().update(bits,now,busy)
            if not bits&2:self.idle_stop_sent=False
            if action=='stop':self.idle_stop_sent=True
            if not busy and bits&2 and bits==self.stable and now-self.changed>=self.debounce and not self.idle_stop_sent:
                self.idle_stop_sent=True;return 'stop'
            return action
    return Gate()


class CancelDiscardMixin:
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.gate=cancel_gate(type(self.gate))

    def _assert_discard_quiescent(self,path):
        if self.process is not None:raise RuntimeError('Child still owns scan')
        report=path/'scan/completion.json'
        if not report.is_file():raise RuntimeError('Scan cleanup record unavailable; defer deletion')
        data=json.loads(report.read_text());cleanup=data.get('cleanup',{})
        camera=cleanup.get('camera',{})
        if not (cleanup.get('motor_stop_confirmed') is True and camera.get('camera_release_confirmed') is True
                and camera.get('no_active_capture_process') is True and data.get('driver_healthy') is True
                and data.get('quarantined_buffers')==0 and not data.get('recovery_hold')):
            raise RuntimeError('Scan hardware cleanup not verified; defer deletion')
        import fcntl
        with open('/tmp/laser_motor_object_scan.lock','a') as owner:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if subprocess.run(['fuser','/dev/video0'],capture_output=True,timeout=5).returncode!=1:
                raise RuntimeError('Camera still occupied; defer deletion')

    def request_stop(self):
        if self.process is None:
            if self.run is None:
                latest=self.root/'latest_result.json'
                if not latest.is_file():return
                record=json.loads(latest.read_text());rid=record.get('run_id')
                if not isinstance(rid,str) or not re.fullmatch('[0-9a-f]{32}',rid):return
                candidate=self.root/'runs'/rid
                if not candidate.is_dir():return
                self.run_id,self.run=rid,candidate
            self.cancelled=True;self.finish('failed','User stop requested');return
        super().request_stop()
        if self.stage=='reconstructing_on_zynq' and self.process.poll() is None:
            # Acquisition already closed camera/DMA before this math-only child.
            self.process.terminate()

    def finish(self,state,error=None):
        if not self.cancelled:return super().finish(state,error)
        path=Path(self.run);runs=(self.root/'runs').resolve()
        if path.is_symlink() or path.resolve().parent!=runs or not re.fullmatch('[0-9a-f]{32}',path.name):
            raise RuntimeError('Refusing to discard outside this service run directory')
        try:self._assert_discard_quiescent(path)
        except Exception as exc:
            super().finish('failed','Discard pending hardware cleanup: '+str(exc))
            return
        rid=self.run_id;count=sum(1 for v in path.rglob('*') if v.is_file())
        event=uuid.uuid4().hex
        record={'schema_version':1,'session_id':self.session_id,'run_id':rid,'state':'failed',
                'discarded':True,'discard_event_id':event,'error':'User cancelled; scan discarded',
                'result_kind':'diagnostic_preview','diagnostic_preview_only':True,
                'alignment_validated':False,'metric_accuracy_accepted':False}
        shutil.rmtree(path)
        from board_button_scan_service import save
        save(self.root/'latest_result.json',record)
        with (self.root/'discard_events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps({'run_id':rid,'event_id':event,'removed_files':count,'hardware_cleanup_verified':True})+'\n')
        self.run=None;self.run_id=None;self.cancelled=False
        self.status('ready',discarded_run_id=rid)
