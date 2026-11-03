"""Per-pose bounded exposure capture with an explicit V4L2 exposure value.

Each capture restores exposure and releases the camera before returning.
The motor orchestration remains in rotation_scan; this module never moves it.
The legacy default remains 20; the current PL scene explicitly uses 5.
"""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys


class PerPoseExposureCamera:
    def __init__(self, worker_source, exposure=20, timeout_s=35):
        if isinstance(exposure, bool) or not isinstance(exposure, int) or not 1 <= exposure <= 0x7fffffff:
            raise ValueError('Exposure must be a positive V4L2 signed-32-bit integer')
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s) or not 1 <= timeout_s <= 40):
            raise ValueError('Capture deadline must be finite and between 1 and 40 seconds')
        compile(worker_source, 'fixed_exposure_worker.py', 'exec')
        self.source = worker_source
        self.exposure = exposure
        self.timeout = timeout_s
        self.root = None
        self.reports = []
        self.uncertain = False
        self.proc = None
        self.cleanup = {}
        self.capture_blocked = False

    @staticmethod
    def _verified_record(folder, expected_exposure=None):
        record = json.loads((folder / 'capture.json').read_text())
        if (record['status'] != 'completed'
                or not record['camera_release_completed']
                or not record['ae_exposure_restoration_verified']
                or record['restore_errors']):
            raise RuntimeError('Camera capture or restoration was not verified')
        frames = record['frames'] + record['bright_frames']
        expected = {'raw_%02d.png' % i for i in range(3)}
        expected.update('%s_%02d.png' % (phase, i)
                        for phase in ('bright_before', 'bright_after') for i in range(3))
        if len(frames) != 9 or {f['file'] for f in frames} != expected:
            raise RuntimeError('Camera record must contain all nine unique original images')
        if expected_exposure is not None:
            if record.get('exposure_trial') != expected_exposure:
                raise RuntimeError('Camera record exposure differs from the requested setting')
            if len(record['frames']) != 3 or {f['file'] for f in record['frames']} != {
                    'raw_%02d.png' % i for i in range(3)}:
                raise RuntimeError('Camera record must identify the three fixed-exposure frames')
            for frame in record['frames']:
                controls = frame.get('controls_readback', {})
                if controls.get('ae') != 1 or controls.get('exp') != expected_exposure:
                    raise RuntimeError('Frame exposure readback differs from the requested setting')
        for frame in frames:
            if hashlib.sha256((folder / frame['file']).read_bytes()).hexdigest() != frame['sha256']:
                raise RuntimeError('Camera image hash mismatch')
        return record

    def start(self, scan_root):
        if self.root is not None:
            raise RuntimeError('Camera adapter already started')
        self.root = Path(scan_root).resolve()
        self.worker = self.root / 'fixed_exposure_worker.py'
        self.worker.write_text(self.source)
        return {'event': 'adapter_prepared', 'physical_camera_opened': False,
                'first_capture_must_pass_before_next_angle': True,
                'worker_sha256': hashlib.sha256(self.source.encode()).hexdigest(),
                'exposure': self.exposure, 'white_balance_frozen': False}

    def capture(self, folder):
        if self.root is None or self.uncertain or self.capture_blocked:
            raise RuntimeError('Camera adapter not ready')
        folder = Path(folder).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError('Capture must stay inside this session')
        folder.mkdir(parents=True, exist_ok=False)
        self.uncertain = True
        with (folder / 'stdout.log').open('w') as log:
            proc = subprocess.Popen(
                [sys.executable, '-u', str(self.worker), str(folder),
                 str(self.exposure), 'three_angle_object_profile'],
                stdout=log, stderr=subprocess.STDOUT)
            self.proc = proc
            try:
                proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.capture_blocked = True
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                try:
                    record = self._verified_record(folder, expected_exposure=self.exposure)
                except Exception as exc:
                    raise RuntimeError('Capture timeout; restoration unconfirmed; do not retry') from exc
                self.reports.append(record)
                self.uncertain = False
                (folder / 'parent_result.json').write_text(json.dumps({
                    'returncode': proc.returncode, 'normal_exit': False,
                    'deadline_expired': True, 'original_hashes_verified': 9,
                    'camera_release_confirmed': True,
                    'exposure_restoration_verified': True,
                    'scan_must_stop': True}, indent=2))
                raise RuntimeError('Capture process exit timed out after verified image saving, '
                                   'exposure restoration and camera release; scan stopped; do not retry')
        record = self._verified_record(folder, expected_exposure=self.exposure)
        self.reports.append(record)
        self.uncertain = False
        if proc.returncode != 0:
            self.capture_blocked = True
            raise RuntimeError('Capture process exited abnormally after verified camera cleanup; scan stopped')
        return {'source': 'hardware_camera', 'images': [
            dict(frame, file=str(folder / frame['file'])) for frame in record['frames']],
            'capture_record': str(folder / 'capture.json'),
            'exposure': self.exposure, 'exposure_restored': True,
            'camera_release_confirmed': True, 'white_balance_frozen': False}

    def close(self):
        self.cleanup = {'camera_release_confirmed': bool(self.reports) and not self.uncertain,
                        'exposure_restoration_verified': bool(self.reports) and not self.uncertain,
                        'capture_count': len(self.reports),
                        'capture_blocked_after_failure': self.capture_blocked,
                        'no_active_capture_process': self.proc is None or self.proc.poll() is not None}
        if self.uncertain:
            raise RuntimeError('Camera release/restoration unconfirmed after capture failure')
        return self.cleanup
