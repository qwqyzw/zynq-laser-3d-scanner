"""Responsive BTN0 acknowledgement and one-shot verification, using frozen61.

No acquisition, geometry, camera timing, motor or reconstruction math changes.
The original child still performs its full independent checks before hardware.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
BASE=PROJECT/'61_current_axis_laser_release_20261024'
BASE_PIN='33b50a6ecf2c98923f078506a45cee8ef4d6f391b3ecc64e307c057e30a334db'
sys.path.insert(0,str(BASE))
import fast_button_service as legacy


def own_identity(expected):
    manifest=ROOT/'startup_manifest.json'
    if legacy.digest(manifest)!=expected:
        raise ValueError('Responsive startup manifest changed')
    data=legacy.load(manifest)
    if data['base_manifest_sha256']!=BASE_PIN or legacy.digest(BASE/'SHA256.json')!=BASE_PIN:
        raise ValueError('Frozen61 binding changed')
    for name,wanted in data['files'].items():
        if Path(name).name!=name or (ROOT/name).is_symlink() or legacy.digest(ROOT/name)!=wanted:
            raise ValueError('Responsive startup source changed: '+name)
    if data['files'].get('responsive_button_service.py')!=legacy.digest(Path(__file__)):
        raise ValueError('Responsive startup entry is not bound')
    return data


class AcknowledgingGate:
    """Decorate the unchanged original gate; no new start or stop decisions."""
    def __init__(self,gate,callback):
        self.gate=gate
        self.callback=callback
        self.acknowledged=False
    def __getattr__(self,name):
        return getattr(self.gate,name)
    def update(self,bits,now,busy):
        action=self.gate.update(bits,now,busy)
        if busy:
            self.acknowledged=False
        elif bits==1 and self.gate.stable==1 and self.gate.armed and not self.acknowledged:
            self.acknowledged=True
            self.callback('button_pressed',now)
        elif bits==0 and self.gate.stable==0 and self.acknowledged:
            self.acknowledged=False
            if action is None:self.callback('released_before_start',now)
        return action


class Coordinator(legacy.Coordinator):
    def __init__(self,*args,startup_manifest_sha256,**kwargs):
        self.startup_manifest_sha256=startup_manifest_sha256
        self._launch_verification=None
        self.button_pressed_monotonic=None
        super().__init__(*args,**kwargs)
        self.gate=AcknowledgingGate(self.gate,self._button_feedback)

    def _button_feedback(self,event,now):
        if event=='button_pressed':
            self.button_pressed_monotonic=now
            self.status('button_pressed',message='BTN0 received; keep holding until one second',
                        hold_seconds=self.gate.hold,monotonic_s=now)
        elif self.state=='button_pressed':
            self.status('ready',message='Released before start; hold BTN0 for one second')

    def verified_current(self):
        if self._launch_verification is not None:
            # This cache exists only inside this synchronous start() call.
            # It is never retained while idle or reused for reconstruction.
            if legacy.digest(self.config['config_file'])!=self.config_sha256:
                raise ValueError('Configuration changed during start')
            return self._launch_verification
        return super().verified_current()

    def start(self):
        if self.process is not None or self.exiting:return
        accepted=time.monotonic()
        self.status('starting',message='BTN0 accepted; verifying project before camera startup',monotonic_s=accepted)
        own_identity(self.startup_manifest_sha256)
        config,artifacts=super().verified_current()
        verified=time.monotonic()
        if self.exiting or (self.gpio is not None and int(self.gpio.read(0))&2):
            self.status('ready',message='Start cancelled before child launch')
            return
        self._launch_verification=(config,artifacts)
        try:
            self.run_id=uuid.uuid4().hex
            self.run=self.root/'runs'/self.run_id
            self.run.mkdir(parents=True,exist_ok=False)
            self.cancelled=False
            # Preserve the exact frozen61 CLI; inherited launch routes it to61.
            project=self.config['project']
            args=[sys.executable,'-B','-u',str(project/'09_tools/object_board_scan.py'),
                  '--project',str(project),'--button-release',str(self.config['overlay_release']),
                  '--output',str(self.run/'scan'),'--client-date',self.config['client_date'],
                  '--roi',*map(str,self.config['roi']),'--exposure',str(self.config['exposure']),
                  '--max-angle',str(self.config['max_angle']),'--run-board']
            (self.run/'startup_source.py').write_bytes(Path(__file__).read_bytes())
            legacy.original_service.save(self.run/'startup_binding.json',{
                'startup_manifest_sha256':self.startup_manifest_sha256,
                'startup_source_sha256':legacy.digest(Path(__file__)),
                'base_manifest_sha256':BASE_PIN,'acquisition_release':str(BASE),
                'child_full_verification_preserved':True,'camera_settle_seconds_unchanged':8,
                'formal_angular_step_deg':5,'dual_exposure_enabled':False})
            self.launch(args,'acquiring')
            legacy.original_service.save(self.run/'startup_timing.json',{
                'button_stable_press_monotonic_s':self.button_pressed_monotonic,
                'button_accepted_monotonic_s':accepted,'verification_completed_monotonic_s':verified,
                'child_launched_monotonic_s':time.monotonic(),
                'full_parent_inspections':1,'actual_camera_or_motor_started_by_parent':False})
        finally:
            self._launch_verification=None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--startup-manifest-sha256',required=True)
    parser.add_argument('--load-board',action='store_true')
    parser.add_argument('--serve-board',action='store_true')
    args=parser.parse_args()
    own_identity(args.startup_manifest_sha256)
    config,artifacts=legacy.inspect_config(args.config)
    if config['fast_release']!=BASE or artifacts['fast_release']['manifest_sha256']!=BASE_PIN:
        raise ValueError('Responsive start supports frozen61 only')
    config['config_file']=args.config.resolve()
    if args.load_board:
        from load_button_overlay import load_board
        print(json.dumps(load_board(config,artifacts),ensure_ascii=False),flush=True)
    if args.serve_board:
        Coordinator(config,artifacts,startup_manifest_sha256=args.startup_manifest_sha256).serve()
    elif not args.load_board:
        print(json.dumps({'status':'responsive_start_files_verified','hardware_accessed':False,
            'base_release':str(BASE),'full_parent_inspections_per_start':1,
            'full_child_inspection_preserved':True,'camera_settle_seconds_unchanged':8}))


if __name__=='__main__':main()
