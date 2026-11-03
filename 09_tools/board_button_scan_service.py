"""Physical-button scan coordinator. Default inspects files; --serve-board arms it.

BTN0: release then hold for one second. BTN1: bounded stop request, not an E-stop.
No overlay is downloaded here. Child DMA owners are never killed or recovered
automatically. All actual XYZ is computed by the existing Zynq-only reconstructor.
"""
import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def inspect_config(path):
    config = load(path)
    for name in ('project', 'overlay_release', 'geometry', 'output_root'):
        value = Path(config[name])
        if not value.is_absolute():
            raise ValueError('Configuration requires absolute path: ' + name)
        config[name] = value.resolve()
    if date.fromisoformat(config['client_date']).isoformat() != config['client_date']:
        raise ValueError('Explicit client date must be YYYY-MM-DD')
    if config['mode'] not in ('diagnostic_preview', 'strict'):
        raise ValueError('Select strict or explicit diagnostic_preview mode')
    if config.get('geometry_sha256') != digest(config['geometry']):
        raise ValueError('Configured geometry SHA256 changed')
    if config['max_angle'] != 60 or config['exposure'] != 5 or config['roi'] != [235, 280, 455, 405]:
        raise ValueError('This commissioning configuration only accepts the tested 0..60 scene settings')
    project = config['project']
    if project / '09_tools' not in Path(__file__).resolve().parents:
        raise ValueError('Service is not from selected project')
    sys.path.insert(0, str(project / '09_tools'))
    import object_board_scan
    _, artifacts, _ = object_board_scan.inspect(project, config['max_angle'], tuple(config['roi']),
                                               config['exposure'], button_release=config['overlay_release'])
    geometry = load(config['geometry'])
    if config['mode'] == 'diagnostic_preview' and geometry.get('diagnostic_preview_only') is not True:
        raise ValueError('Diagnostic mode requires explicitly labelled diagnostic geometry')
    return config, artifacts


class ButtonGate:
    """No queued starts; a fresh stable release is required after each busy period."""
    def __init__(self, debounce=.08, hold=1.0):
        self.debounce, self.hold = debounce, hold
        self.raw = None
        self.changed = 0.0
        self.stable = None
        self.pressed_at = None
        self.armed = False
        self.stop_sent = False

    def update(self, bits, now, busy):
        bits &= 15
        if bits != self.raw:
            self.raw, self.changed = bits, now
        if now - self.changed < self.debounce:
            return None
        if bits != self.stable:
            self.stable = bits
            self.pressed_at = now if bits & 1 else None
            self.stop_sent = False
        if busy:
            self.armed = False
            if bits & 2 and not self.stop_sent:
                self.stop_sent = True
                return 'stop'
            return None
        if bits == 0:
            self.armed = True
        if bits & 2:
            self.armed = False
            return None
        if self.armed and bits & 1 and self.pressed_at is not None and now - self.pressed_at >= self.hold:
            self.armed = False
            return 'start'
        return None


class Coordinator:
    def __init__(self, config, artifacts):
        self.config, self.artifacts = config, artifacts
        self.root = config['output_root']
        self.root.mkdir(parents=True, exist_ok=True)
        self.session_id = uuid.uuid4().hex
        self.run_id = None
        self.process = self.log = self.stage = None
        self.run = None
        self.cancelled = self.exiting = False
        self.state = 'ready'
        self.config_sha256 = digest(config['config_file'])
        self.gate = ButtonGate()
        self.gpio = None
        self.owner = None

    def status(self, state, **extra):
        self.state = state
        save(self.root / 'status.json', {'schema_version': 1, 'session_id': self.session_id,
             'run_id': self.run_id, 'state': state, 'mode': self.config['mode'],
             'client_date': self.config['client_date'], 'process_id': os.getpid(),
             'child_pid': self.process.pid if self.process else None,
             'diagnostic_preview_only': self.config['mode'] == 'diagnostic_preview', **extra})
        if self.gpio is not None:
            leds = 0 if state == 'stopped' else 1 if state == 'ready' else 4 if state.startswith('ready_') else 8 if state in ('failed', 'recovery_required') else 2
            self.gpio.write(8, leds)

    def attach(self):
        import fcntl
        self.owner = open('/tmp/laser_motor_button_service.lock', 'a')
        fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from pynq import PL, Clocks, MMIO, Overlay
        selected = self.artifacts['button_release']['laser_motor_button.bit']
        if Path(str(PL.bitfile_name)).resolve() != Path(selected['absolute_path']).resolve():
            raise ValueError('Button overlay is not loaded; service never downloads it')
        if Clocks.fclk0_mhz != 50.0 or digest(PL.bitfile_name) != selected['sha256']:
            raise ValueError('Loaded button overlay/clock differs')
        overlay = Overlay(selected['absolute_path'], download=False)
        if 'axi_gpio_scan' not in overlay.ip_dict:
            raise ValueError('Button GPIO metadata missing')
        # GPIO does not construct native DMA drivers or touch the camera/UART.
        self.gpio = MMIO(0x41200000, 0x10000)
        self.gpio.write(4, 15)
        self.gpio.write(12, 0)
        self.gpio.write(8, 0)
        self.status('waiting_for_release')

    def start(self):
        if self.process is not None or self.exiting:
            return
        if digest(self.config['config_file']) != self.config_sha256:
            raise ValueError('Configuration changed; restart service explicitly')
        # Recheck immutable runtime/geometry before any child can touch hardware.
        _, artifacts = inspect_config(self.config['config_file'])
        if artifacts['button_release']['manifest_sha256'] != self.artifacts['button_release']['manifest_sha256']:
            raise ValueError('Button release changed while service was armed')
        self.run_id = uuid.uuid4().hex
        self.run = self.root / 'runs' / self.run_id
        self.run.mkdir(parents=True, exist_ok=False)
        self.cancelled = False
        project = self.config['project']
        args = [sys.executable, '-B', '-u', str(project / '09_tools/object_board_scan.py'),
                '--project', str(project), '--button-release', str(self.config['overlay_release']),
                '--output', str(self.run / 'scan'), '--client-date', self.config['client_date'],
                '--roi', *map(str, self.config['roi']), '--exposure', str(self.config['exposure']),
                '--max-angle', str(self.config['max_angle']), '--run-board']
        self.launch(args, 'acquiring')

    def launch(self, args, stage):
        self.stage = stage
        self.log = open(self.run / (stage + '.log'), 'ab', buffering=0)
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=self.log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        self.status(stage)

    def request_stop(self):
        if self.process is None:
            return
        self.cancelled = True
        scan = self.run / 'scan'
        if self.stage == 'acquiring' and scan.is_dir():
            (scan / 'abort.request').touch()
        self.status('stop_requested')

    def finish(self, state, error=None):
        diagnostic = self.config['mode'] == 'diagnostic_preview'
        record = {'schema_version': 1, 'session_id': self.session_id, 'run_id': self.run_id,
                  'state': 'failed' if error else 'ready', 'result_kind': 'diagnostic_preview' if diagnostic else 'pointcloud',
                  'diagnostic_preview_only': diagnostic, 'alignment_validated': not diagnostic,
                  'metric_accuracy_accepted': False}
        if error:
            record['error'] = error
        else:
            viewer = self.run / 'model/viewer'
            manifest = load(viewer / 'export_manifest.json')
            metadata = load(viewer / 'source_meta.json')
            if manifest.get('coordinate_producer') != 'zynq' or manifest.get('test_only') is not False:
                raise ValueError('Refusing non-board completion')
            for name in ('diagnostic_preview_only', 'alignment_validated', 'metric_accuracy_accepted'):
                if type(metadata.get(name)) is not bool or metadata[name] != record[name]:
                    raise ValueError('Completion diagnostic state contradicts board metadata')
            record.update(viewer_relative=viewer.relative_to(self.root).as_posix(),
                          export_manifest_sha256=digest(viewer / 'export_manifest.json'),
                          point_count=manifest['point_statistics']['finite_points'])
        save(self.run / 'result.json', record)
        save(self.root / 'latest_result.json', record)
        self.status(state, error=error)

    def poll(self):
        if self.process is None:
            return
        report_path = self.run / ('scan/completion.json' if self.stage == 'acquiring' else 'model/reconstruction.json')
        if self.process.poll() is None:
            if self.cancelled and self.stage == 'acquiring' and (self.run / 'scan').is_dir():
                (self.run / 'scan/abort.request').touch()
            try:
                if load(report_path).get('recovery_hold') is True:
                    self.status('recovery_required', error='DMA owner retained; manual audited recovery required')
            except (OSError, ValueError):
                pass
            return
        returncode = self.process.returncode
        self.process.stdin.close()
        self.process = None
        self.log.close()
        self.log = None
        report = load(report_path) if report_path.is_file() else {}
        if returncode != 0 or self.cancelled:
            self.finish('failed', 'User stop requested' if self.cancelled else 'Child failed; inspect ' + self.stage + '.log')
            return
        if self.stage == 'acquiring':
            if report.get('status') != 'completed' or report.get('real_hardware_acquisition_verified') is not True:
                self.finish('failed', 'Acquisition did not pass real hardware checks')
                return
            args = [sys.executable, '-B', '-u', str(self.config['project'] / '09_tools/reconstruct_object_on_zynq.py'),
                    '--project', str(self.config['project']), '--scan', str(self.run / 'scan'),
                    '--geometry', str(self.config['geometry']), '--output', str(self.run / 'model'), '--run-board']
            if self.config['mode'] == 'diagnostic_preview':
                args.append('--diagnostic-preview')
            self.launch(args, 'reconstructing_on_zynq')
        else:
            expected = 'board_diagnostic_preview_exported' if self.config['mode'] == 'diagnostic_preview' else 'board_reconstruction_and_export_completed'
            if report.get('status') != expected:
                self.finish('failed', 'Board reconstruction did not produce the requested result kind')
                return
            self.finish('ready_diagnostic_preview' if self.config['mode'] == 'diagnostic_preview' else 'ready_pointcloud')

    def serve(self):
        self.attach()

        def shutdown(signum, frame):
            self.exiting = True
            self.request_stop()

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        while not self.exiting or self.process is not None:
            try:
                self.poll()
            except Exception as error:
                if self.process is not None:
                    self.status('recovery_required', error='Child owner retained: ' + str(error))
                elif self.run is not None:
                    self.finish('failed', str(error))
                else:
                    self.status('failed', error=str(error))
            busy = self.process is not None
            action = self.gate.update(int(self.gpio.read(0)), time.monotonic(), busy or self.exiting)
            if action == 'stop':
                self.request_stop()
            elif action == 'start':
                try:
                    self.start()
                except Exception as error:
                    self.status('failed', error=str(error))
            elif not busy and self.gate.armed and self.state == 'waiting_for_release':
                self.status('ready')
            time.sleep(.02)
        self.gpio.write(8, 0)
        self.status('stopped')
        self.owner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--serve-board', action='store_true')
    args = parser.parse_args()
    config, artifacts = inspect_config(args.config)
    config['config_file'] = args.config.resolve()
    if not args.serve_board:
        print(json.dumps({'status': 'button_config_verified', 'hardware_accessed': False,
                          'mode': config['mode'], 'button_release': artifacts['button_release']['version']}))
        return 0
    Coordinator(config, artifacts).serve()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('Button service rejected: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
