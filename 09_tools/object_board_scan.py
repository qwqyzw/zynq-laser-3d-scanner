"""Explicit, finite object acquisition using the already loaded PL+UART release.

Without --run-board this only checks project files and prints the nominal plan.
The board path never downloads an overlay. Keep its process and FIFO alive if
DMA recovery is unconfirmed. abort.request is checked between bounded actions;
it stops acquisition and does not initiate an automatic return.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace

from check_object_scan_plan import validate_object_route

DEFAULT_PROJECT = Path(__file__).resolve().parents[1]


class AbortRequested(RuntimeError):
    pass


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be a finite number')
    return float(value)


def nominal_plan(max_angle):
    if type(max_angle) is not int or not 5 <= max_angle <= 180 or max_angle % 5:
        raise ValueError('max-angle must be a multiple of five within 5..180 degrees')
    acquire = list(range(0, max_angle + 1, 5))
    returning = list(range(max_angle - 5, -1, -5))
    stages = []
    for anchor in range(0, max_angle, 60):
        local = list(range(0, min(60, max_angle - anchor) + 1, 5))
        stages.append({
            'global_origin_motor_deg': 0,
            'stage_origin_angle_deg': anchor,
            'local_angles_deg': local,
            'global_angles_deg': [anchor + value for value in local],
            'absolute_targets_deg': [anchor + value for value in local],
        })
    audit = validate_object_route(0, acquire, returning, stage_records=stages)
    return {
        'scope': 'nominal_plan_origin_to_be_measured_before_motion',
        'acquisition_angles_deg': acquire, 'return_angles_deg': returning,
        'stage_records': stages, 'route_audit': audit,
        'max_angle_deg': max_angle, 'nominal_step_deg': 5, 'rpm': 1,
        'maximum_transmitted_step_deg': 5.5,
        'feedback_envelope_deg': [-.4, max_angle + .4],
        'expected_capture_batches': len(acquire) + 2,
        'expected_new_PL_transactions': 3 * (len(acquire) + 2),
        'expected_original_images': 9 * (len(acquire) + 2),
        'maximum_FD_attempts': 2 * (len(acquire) - 1),
        'normal_return_then_origin_recapture': True,
        'fault_or_abort_return': False,
        'geometry_or_metric_accuracy_verified': False,
        'physical_cable_clearance_verified': False,
    }


class FiniteEnvelopeMixin:
    """Guard every FD immediately before the existing UART implementation."""
    def __init__(self, *args, **kwargs):
        self.object_origin = None
        self.object_max_angle = None
        self.fd_attempts = 0
        self.fd_limit = 0
        super().__init__(*args, **kwargs)

    def arm_object_envelope(self, origin, max_angle):
        if self.object_origin is not None:
            raise RuntimeError('The global motor origin cannot be changed during this session')
        plan = nominal_plan(max_angle)
        self.object_origin = finite(origin, 'motor origin')
        self.object_max_angle = max_angle
        self.fd_limit = plan['maximum_FD_attempts']

    def _exchange(self, operation, plan=None):
        if operation == 2:
            if self.object_origin is None or not self.prepared:
                raise RuntimeError('Finite object envelope has not been armed')
            delta = finite(plan['quantized_delta_deg'], 'quantized move')
            target = finite(plan['quantized_target_motor_deg'], 'quantized target')
            requested = finite(plan['requested_target_motor_deg'], 'requested target')
            transmitted_delta = (plan['pulses'] * self.geometry.degrees_per_pulse *
                                 (1 if plan['direction'] == 0 else -1))
            if plan['rpm'] != 1 or abs(delta - transmitted_delta) > 1e-9:
                raise RuntimeError('FD command geometry or speed differs from its guarded plan')
            relative = target - self.object_origin
            requested_relative = requested - self.object_origin
            if abs(delta) > 5.5 + 1e-9:
                raise RuntimeError('Object move exceeds the 5.5-degree transmission bound')
            if not -.4 <= relative <= self.object_max_angle + .4:
                raise RuntimeError('Quantized target leaves the global feedback envelope')
            if not -1e-9 <= requested_relative <= self.object_max_angle + 1e-9:
                raise RuntimeError('Requested target leaves the nominal global scan envelope')
            if self.fd_attempts >= self.fd_limit:
                raise RuntimeError('Finite movement attempt budget exhausted')
            self.fd_attempts += 1
        elif operation == 1 and self.object_origin is not None:
            raise RuntimeError('Automatic re-enable during object acquisition is forbidden')
        return super()._exchange(operation, plan)


def save(path, data):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def quiet_save(path, data):
    try:
        save(path, data)
    except BaseException as exc:
        data['report_write_error'] = repr(exc)


def progress(*parts):
    try:
        print(*parts, flush=True)
    except BaseException:
        pass


def abort_check(root):
    if (root / 'abort.request').exists():
        raise AbortRequested('abort.request found; stop without automatic return')


def read_motor_stationary(motor, root):
    """Only UART reads before the camera quality gate; no prepare/enable/move."""
    samples = []
    for index in range(3):
        abort_check(root)
        status = motor.status()
        position = motor.position()
        if (not status['position_reached'] or status['stall_detected'] or
                status['stall_protection_triggered']):
            raise RuntimeError('Motor is moving or reports a fault before camera preflight')
        value = finite(position['motor_position_deg'], 'preflight motor position')
        samples.append({'status': status, 'position': position, 'sample_monotonic': time.monotonic()})
        if index < 2:
            time.sleep(.2)
    values = [sample['position']['motor_position_deg'] for sample in samples]
    if max(values) - min(values) > .1:
        raise RuntimeError('Motor is not stationary before camera preflight')
    return samples


def assert_motor_state(status, position, origin, max_angle):
    if not status['enabled'] or not status['position_reached']:
        raise RuntimeError('Motor is not enabled and settled')
    if status['stall_detected'] or status['stall_protection_triggered']:
        raise RuntimeError('Motor reports a fault; automatic motion is cancelled')
    value = finite(position, 'motor position')
    if not -.4 <= value - origin <= max_angle + .4:
        raise RuntimeError('Actual motor feedback leaves the global envelope')


def assert_idle(diagnostics, *, completed=None, require_completed=False):
    status = diagnostics['status']
    if type(status) is not int or status & 0xD:
        raise RuntimeError('Core busy/error/reset state is not quiescent')
    if require_completed and not status & 2:
        raise RuntimeError('Core has no confirmed DONE state')
    if diagnostics.get('quarantined_buffers', 0) != 0:
        raise RuntimeError('DMA buffers remain quarantined')
    if completed is not None and diagnostics['completed_frames'] != completed:
        raise RuntimeError('Unexpected core frame counter; exclusive ownership is not established')
    dma = diagnostics['dma']
    for channel in ('mm2s', 's2mm'):
        sr, cr = dma[channel + '_sr'], dma[channel + '_cr']
        if sr & 0x778 or cr & 4 or not sr & 3:
            raise RuntimeError(channel + ' DMA is active, resetting, SG or in error')
        if require_completed and (sr & 0x1003 != 0x1002):
            raise RuntimeError(channel + ' DMA completion/idle flags disagree')
    if require_completed and dma['received_bytes'] != 2560:
        raise RuntimeError('DMA receive length differs from 640 uint32 words')


def load_modules(project, *, board=False):
    runtime_path = project / '04_runtime'
    sys.path.insert(0, str(runtime_path))
    modules = {}
    names = ['pl_runtime', 'laser_stripe']
    if board:
        names += ['motor_uartlite', 'pl_scan_camera']
    for name in names:
        module = importlib.import_module(name)
        if Path(module.__file__).resolve().parent != runtime_path.resolve():
            raise RuntimeError('Module imported from another project: ' + name)
        modules[name] = module
    return modules


def inspect(project, max_angle, roi, exposure, button_release=None):
    modules = load_modules(project)
    modules['laser_stripe'].StripeConfig(*roi, threshold_q8=3072, min_run=8)
    if type(exposure) is not int or not 1 <= exposure <= 0x7FFFFFFF:
        raise ValueError('Exposure must be a positive signed-32-bit V4L2 value')
    artifacts = modules['pl_runtime'].inspect_integrated_artifacts(project)
    if button_release is not None:
        from button_overlay_runtime import inspect_button_release
        artifacts['button_release'] = inspect_button_release(project, button_release)
    return modules, artifacts, nominal_plan(max_angle)


def attach_loaded_runtime(project, modules, artifacts):
    """Read registers and bind metadata, without a bitstream download or DMA start."""
    os.environ['XILINX_XRT'] = '/usr'
    from pynq import Overlay, PL, Clocks, MMIO
    board = Path('/sys/firmware/devicetree/base/chosen/pynq_board').read_bytes().rstrip(b'\0').decode()
    if board != 'Pynq-Z2':
        raise RuntimeError('Expected PYNQ-Z2')
    os.environ['BOARD'] = board
    if 'button_release' in artifacts:
        bit_record = artifacts['button_release']['laser_motor_button.bit']
    else:
        bit_record = artifacts['integrated_release']['laser_motor.bit']
    expected = Path(bit_record['absolute_path']).resolve()

    def identity():
        current = Path(PL.bitfile_name).resolve()
        if current != expected:
            raise RuntimeError('The loaded bitfile path is not this project integrated release')
        digest = hashlib.sha256(current.read_bytes()).hexdigest()
        if digest != bit_record['sha256']:
            raise RuntimeError('Loaded bitfile path contents differ from the validated release')
        if Clocks.fclk0_mhz != 50.0:
            raise RuntimeError('Expected the integrated 50 MHz clock')
        return {'bitfile': str(current), 'sha256': digest,
                'timestamp': str(PL.timestamp), 'fclk0_mhz': Clocks.fclk0_mhz}

    before = identity()
    core, dma = MMIO(0x43C00000, 0x10000), MMIO(0x40400000, 0x10000)

    def raw_snapshot():
        if core.read(8) != 0x4C535431 or core.read(12) != 0x10000:
            raise RuntimeError('Actual core ID/version differs from the frozen stripe design')
        return {'status': int(core.read(4)), 'completed_frames': int(core.read(0x38)),
                'dma': {'mm2s_cr': int(dma.read(0)), 'mm2s_sr': int(dma.read(4)),
                        's2mm_cr': int(dma.read(0x30)), 's2mm_sr': int(dma.read(0x34)),
                        'received_bytes': int(dma.read(0x58))}}

    initial = raw_snapshot()
    assert_idle(initial)
    overlay = Overlay(str(expected), download=False)
    if not overlay.is_loaded() or 'axi_uartlite_0' not in overlay.ip_dict:
        raise RuntimeError('Current physical overlay and expected UART metadata disagree')
    if identity() != before:
        raise RuntimeError('Overlay identity changed while binding existing metadata')
    after = raw_snapshot()
    assert_idle(after, completed=initial['completed_frames'])
    driver = modules['laser_stripe'].LaserStripe.from_overlay(overlay, timeout=10)
    assert_idle(driver.diagnostics(), completed=initial['completed_frames'])
    return SimpleNamespace(overlay=overlay, driver=driver, artifacts=artifacts,
                           initial_diagnostics=initial, identity=before,
                           read_identity=identity)


def verify_capture(root, folder, batch, driver, expected_counter):
    if batch.get('source') != 'hardware_camera':
        raise RuntimeError('Capture did not identify the physical camera')
    if batch.get('stripe_coordinate_source') != 'supplied_PL_driver':
        raise RuntimeError('Capture did not use the supplied PL driver')
    if not batch['stripe_quality']['image_quality_gate_passed']:
        raise RuntimeError('Existing image quality gate failed')
    if len(batch.get('images', [])) != 3:
        raise RuntimeError('Need three original fixed-exposure images')
    for frame in batch['images']:
        image = Path(frame['file']).resolve()
        if not image.is_relative_to(root) or not image.is_file():
            raise RuntimeError('Original frame is missing or outside the session')
        if hashlib.sha256(image.read_bytes()).hexdigest() != frame['sha256']:
            raise RuntimeError('Original image fingerprint differs')
    rows = json.loads((folder / 'pl_transactions.json').read_text(encoding='utf-8'))
    wanted = [(expected_counter + offset) & 0xFFFFFFFF for offset in (1, 2, 3)]
    if [row['frame'] for row in rows] != [0, 1, 2] or [row['completed_frames'] for row in rows] != wanted:
        raise RuntimeError('PL frame counter is not continuous across this capture')
    for row in rows:
        delta = row.get('max_pixel_difference', float('inf'))
        if not row.get('valid_equal') or not isinstance(delta, (int, float)) or not math.isfinite(delta) or not 0 <= delta <= .25:
            raise RuntimeError('Frozen PS/PL coordinate comparison failed')
        output = folder / ('pl_%02d_actual.bin' % row['frame'])
        if not output.is_file() or output.stat().st_size != 2560:
            raise RuntimeError('PL output binary is missing or incomplete')
    if not driver.healthy or driver.quarantined_buffer_count:
        raise RuntimeError('PL driver requires recovery')
    assert_idle(driver.diagnostics(), completed=wanted[-1], require_completed=True)
    return wanted[-1]


def shutdown_camera(camera):
    """Stop a child left by interruption before recording cleanup uncertainty."""
    child = getattr(camera, 'proc', None)
    child_record = {}
    if child is not None and child.poll() is None:
        child_record['interrupted_capture_child_terminated'] = True
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)
    try:
        return {**camera.close(), **child_record}
    except BaseException as exc:
        return {**getattr(camera, 'cleanup', {}), **child_record,
                'camera_close_error': repr(exc),
                'no_active_capture_process': child is None or child.poll() is not None}


def run_acquisition(root, runtime, motor, camera, plan, *, client_date,
                    roi, exposure, hardware=False, source_hashes=None):
    """One session owner. Offline tests explicitly leave hardware=False."""
    report = {
        'status': 'starting', 'hardware_accessed': hardware, 'simulation': not hardware,
        'client_date': client_date, 'device_wall_time_unverified': datetime.now(timezone.utc).isoformat(),
        'plan': plan, 'roi': list(roi), 'exposure': exposure,
        'requested_angles_deg': plan['acquisition_angles_deg'],
        'source_hashes': source_hashes or {}, 'global_origin_motor_deg': None,
        'overlay_before': runtime.identity, 'initial_diagnostics': runtime.initial_diagnostics,
        'baseline_completed_frames': runtime.initial_diagnostics['completed_frames'],
        'points': [], 'return_points': [], 'cleanup': {},
        'geometry_or_metric_accuracy_verified': False,
        'independent_turntable_angle_accuracy_verified': False,
        'physical_cable_clearance_verified': False,
    }
    if 'button_release' in getattr(runtime, 'artifacts', {}):
        selected = runtime.artifacts['button_release']
        report['button_release'] = {key: selected[key] for key in ('relative_directory', 'manifest_sha256')}
    driver = runtime.driver
    counter = report['baseline_completed_frames']
    started = time.monotonic()

    def checkpoint():
        report['motion_log'] = motor.motion_log
        report['read_transactions'] = motor.transactions
        report['elapsed_s'] = time.monotonic() - started
        save(root / 'completion.json', report)

    def event(kind, **fields):
        with (root / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'event': kind, 'elapsed_s': time.monotonic() - started,
                                     **fields}, ensure_ascii=False) + '\n')

    def ensure_owner():
        if runtime.read_identity() != runtime.identity:
            raise RuntimeError('Overlay identity changed during object acquisition')
        if not driver.healthy or driver.quarantined_buffer_count:
            raise RuntimeError('DMA driver is unhealthy or owns quarantined buffers')
        assert_idle(driver.diagnostics(), completed=counter, require_completed=counter != report['baseline_completed_frames'])

    def capture(folder):
        nonlocal counter
        abort_check(root)
        ensure_owner()
        report['active_capture_folder'] = str(folder)
        checkpoint()
        batch = camera.capture(folder)
        counter = verify_capture(root, folder, batch, driver, counter)
        ensure_owner()
        report['last_verified_completed_frames'] = counter
        report['completed_capture_batches'] = report.get('completed_capture_batches', 0) + 1
        report.pop('active_capture_folder', None)
        abort_check(root)
        return batch

    def capture_pose(folder, settled_position):
        batch = capture(folder)
        status = motor.status()
        after = motor.position()['motor_position_deg']
        assert_motor_state(status, after, origin, plan['max_angle_deg'])
        if abs(after - settled_position) > .1:
            raise RuntimeError('Motor position changed by more than 0.1 degree during capture')
        return batch, status, after

    try:
        event('starting')
        checkpoint()
        abort_check(root)
        report['motor_before_camera_readonly'] = read_motor_stationary(motor, root)
        report['camera_ready'] = camera.start(root)
        event('camera_ready')
        report['preflight_capture'] = capture(root / 'preflight' / 'images')
        event('preflight_capture_passed')
        checkpoint()
        abort_check(root)
        report['motor_ready'] = motor.prepare()
        origin = finite(report['motor_ready']['initial_position_deg'], 'global motor origin')
        report['global_origin_motor_deg'] = origin
        report['motor_origin_deg'] = origin
        motor.arm_object_envelope(origin, plan['max_angle_deg'])
        report['bound_absolute_targets_deg'] = [origin + angle for angle in plan['acquisition_angles_deg']]
        report['bound_stage_records'] = [
            {**stage, 'global_origin_motor_deg': origin,
             'absolute_targets_deg': [origin + angle for angle in stage['global_angles_deg']]}
            for stage in plan['stage_records']]
        report['bound_route_audit'] = validate_object_route(
            origin, plan['acquisition_angles_deg'], plan['return_angles_deg'],
            stage_records=report['bound_stage_records'])
        event('motor_ready', global_origin_motor_deg=origin)
        report['status'] = 'acquiring'
        checkpoint()
        for index, angle in enumerate(plan['acquisition_angles_deg']):
            abort_check(root)
            ensure_owner()
            stage = max(0, (angle - 1) // 60)
            point = {'index': index, 'global_angle_deg': angle,
                     'requested_table_relative_deg': angle,
                     'global_origin_motor_deg': origin, 'target_motor_deg': origin + angle,
                     'stage_index': stage, 'stage_origin_angle_deg': stage * 60,
                     'stage_relative_deg': angle - stage * 60, 'status': 'moving'}
            report['points'].append(point)
            checkpoint()
            event('move_start', index=index, global_angle_deg=angle, stage_index=stage)
            if index == 0:
                moved = {'initial_settle_reused': True, 'motion_command_required': False,
                         'settled': report['motor_ready']['initial_settle'],
                         'motor_position_deg': origin}
            else:
                moved = motor.move_to(origin + angle, rpm=1)
            measured = finite(moved['motor_position_deg'], 'settled feedback')
            assert_motor_state(motor.status(), measured, origin, plan['max_angle_deg'])
            point.update(move=moved, feedback_relative_deg=measured - origin,
                         table_relative_deg_from_motor_feedback=measured - origin,
                         motor_position_before_capture_deg=measured, status='capturing')
            event('settled', index=index, motor_position_deg=measured)
            checkpoint()
            batch, status, after = capture_pose(root / ('point_%03d' % index) / 'images', measured)
            point.update(capture=batch, motor_status_after_capture=status,
                         motor_position_after_capture_deg=after,
                         capture_drift_deg=after - measured,
                         position_change_during_capture_deg=after - measured, status='completed')
            save(root / ('point_%03d' % index) / 'point.json', point)
            event('point_saved', index=index, feedback_relative_deg=measured - origin)
            checkpoint()
            progress('OBJECT_POINT', index + 1, '/', len(plan['acquisition_angles_deg']),
                     'requested_deg', angle, 'feedback_deg', measured - origin,
                     'common_columns', batch['stripe_quality']['common_quality_columns'])
        report['acquisition_completed'] = True
        report['status'] = 'returning'
        checkpoint()
        for index, angle in enumerate(plan['return_angles_deg']):
            abort_check(root)
            ensure_owner()
            current = motor.position()['motor_position_deg']
            assert_motor_state(motor.status(), current, origin, plan['max_angle_deg'])
            row = {'index': index, 'global_angle_deg': angle, 'target_motor_deg': origin + angle,
                   'status': 'moving'}
            report['return_points'].append(row)
            checkpoint()
            event('return_move_start', index=index, global_angle_deg=angle)
            moved = motor.move_to(origin + angle, rpm=1)
            measured = finite(moved['motor_position_deg'], 'return feedback')
            assert_motor_state(motor.status(), measured, origin, plan['max_angle_deg'])
            row.update(move=moved, feedback_relative_deg=measured - origin, status='completed')
            event('return_settled', index=index, feedback_relative_deg=measured - origin)
            checkpoint()
            progress('OBJECT_RETURN', index + 1, '/', len(plan['return_angles_deg']),
                     'feedback_deg', measured - origin)
        report['return_to_origin_completed'] = True
        batch, status, after = capture_pose(root / 'return_origin' / 'images', measured)
        report['return_origin_capture'] = {
            'capture': batch, 'motor_position_before_capture_deg': measured,
            'motor_position_after_capture_deg': after, 'motor_status_after_capture': status,
            'capture_drift_deg': after - measured, 'feedback_relative_deg': measured - origin}
        ensure_owner()
        if report['completed_capture_batches'] != plan['expected_capture_batches']:
            raise RuntimeError('Completed capture count differs from the finite plan')
        event('return_origin_recaptured')
        report['status'] = 'completed'
    except BaseException as exc:
        report['status'] = ('aborted' if isinstance(exc, AbortRequested) else
                            'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed')
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        report['traceback'] = traceback.format_exc()
        if hasattr(exc, 'samples'):
            report['failed_settle_samples'] = exc.samples
        if report['points'] and report['points'][-1]['status'] != 'completed':
            report['points'][-1]['status'] = 'failed'
        if report['return_points'] and report['return_points'][-1]['status'] != 'completed':
            report['return_points'][-1]['status'] = 'failed'
        report['automatic_return_after_failure'] = False
    finally:
        # Every optional report or camera operation follows the stop attempt.
        try:
            report['cleanup']['motor_stop'] = motor.stop()
            report['cleanup']['motor_stop_confirmed'] = True
        except BaseException as exc:
            report['cleanup']['motor_stop_confirmed'] = False
            report['cleanup']['motor_stop_error'] = repr(exc)
        try:
            report['cleanup']['camera'] = shutdown_camera(camera)
        except BaseException as exc:
            report['cleanup']['camera'] = {'camera_close_error': repr(exc)}
        try:
            motor.close()
            report['cleanup']['motor_client_closed'] = True
        except BaseException as exc:
            report['cleanup']['motor_close_error'] = repr(exc)
        if report['status'] == 'completed' and (
                not report['cleanup'].get('motor_stop_confirmed') or
                not report['cleanup'].get('motor_client_closed') or
                report['cleanup']['camera'].get('camera_close_error') or
                not report['cleanup']['camera'].get('camera_release_confirmed') or
                not report['cleanup']['camera'].get('exposure_restoration_verified') or
                not report['cleanup']['camera'].get('no_active_capture_process')):
            report['status'] = 'cleanup_failed'
        report['motion_log'] = motor.motion_log
        report['read_transactions'] = motor.transactions
        report['fd_attempts'] = motor.fd_attempts
        report['driver_healthy'] = driver.healthy
        report['quarantined_buffers'] = driver.quarantined_buffer_count
        try:
            report['diagnostics'] = driver.diagnostics()
            report['overlay_after'] = runtime.read_identity()
            report['same_overlay_throughout'] = report['overlay_after'] == runtime.identity
            if report['status'] == 'completed':
                assert_idle(report['diagnostics'], completed=counter, require_completed=True)
                if not report['same_overlay_throughout'] or not driver.healthy or driver.quarantined_buffer_count:
                    raise RuntimeError('Overlay or DMA ownership changed during cleanup')
        except BaseException as exc:
            report['final_diagnostics_error'] = repr(exc)
            if report['status'] == 'completed':
                report['status'] = 'cleanup_failed'
        report['elapsed_s'] = time.monotonic() - started
        report['real_hardware_acquisition_verified'] = hardware and report['status'] == 'completed'
        quiet_save(root / 'completion.json', report)
        progress('OBJECT_RESULT', report['status'])
    return report


def snapshot_sources(project, root, artifacts):
    snapshot = root / 'tested_sources'
    hashes = {}
    sources = list((project / '04_runtime').glob('*.py'))
    sources += [project / '04_runtime/rig_config.json',
                project / '14_integrated_release_20261020/SHA256.json',
                Path(__file__).resolve(), Path(__file__).with_name('check_object_scan_plan.py'),
                project / '09_tools/run_integrated_board.py']
    if 'button_release' in artifacts:
        selected = artifacts['button_release']
        sources += [Path(selected['manifest_path'])]
        sources += [project / name for name in selected['source_sha256']]
    sources = list(dict.fromkeys(sources))
    for source in sources:
        relative = source.resolve().relative_to(project)
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    save(root / 'source_fingerprints.json', hashes)
    save(root / 'verified_artifacts.json', artifacts)
    return hashes


def execute_board(project, output, client_date, roi, exposure, modules, artifacts, plan):
    if date.fromisoformat(client_date).isoformat() != client_date:
        raise ValueError('Supply client date in YYYY-MM-DD format')
    output.mkdir(parents=True, exist_ok=False)
    hashes = snapshot_sources(project, output, artifacts)
    save(output / 'nominal_plan.json', plan)
    runtime = None
    motor = None
    camera = None
    entered_acquisition = False
    report = {'status': 'preflight_starting', 'client_date': client_date, 'cleanup': {},
              'source_hashes': hashes, 'plan': plan, 'hardware_requested': True}
    owner_file = None
    try:
        import fcntl
        owner_file = open('/tmp/laser_motor_object_scan.lock', 'a')
        fcntl.flock(owner_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        camera_owner = subprocess.run(['fuser', '/dev/video0'], capture_output=True, text=True, timeout=5)
        if camera_owner.returncode not in (0, 1) or camera_owner.stdout.strip():
            raise RuntimeError('Camera is occupied or camera ownership cannot be checked')
        abort_check(output)
        modules = load_modules(project, board=True)
        runtime = attach_loaded_runtime(project, modules, artifacts)
        Motor = type('ObjectEnvelopeMotor', (FiniteEnvelopeMixin,
                     modules['motor_uartlite'].UARTLiteAngleController), {})
        motor = Motor(runtime.overlay, address=1)
        worker_source = (project / '04_runtime/fixed_exposure_worker.py').read_text(encoding='utf-8')
        camera = modules['pl_scan_camera'].PLStripeCheckedCamera(
            worker_source, roi, runtime.driver, verify_ps=True, exposure=exposure, timeout_s=40)
        entered_acquisition = True
        report = run_acquisition(output, runtime, motor, camera, plan, client_date=client_date,
                                 roi=roi, exposure=exposure, hardware=True, source_hashes=hashes)
    except BaseException as exc:
        report.update(status='failed', error=repr(exc), traceback=traceback.format_exc())
        quiet_save(output / 'completion.json', report)
        progress('OBJECT_RESULT', 'failed')
    finally:
        # This never switches overlay, resets the core automatically, or drops
        # retained DMA buffers merely because SSH/output has failed.
        try:
            if not entered_acquisition:
                if motor is not None:
                    try:
                        report['cleanup']['motor_stop'] = motor.stop()
                        report['cleanup']['motor_stop_confirmed'] = True
                    except BaseException as exc:
                        report['cleanup']['motor_stop_error'] = repr(exc)
                    try:
                        motor.close()
                        report['cleanup']['motor_client_closed'] = True
                    except BaseException as exc:
                        report['cleanup']['motor_close_error'] = repr(exc)
                    report['motion_log'] = motor.motion_log
                    report['read_transactions'] = motor.transactions
                if camera is not None:
                    report['cleanup']['camera'] = shutdown_camera(camera)
                quiet_save(output / 'completion.json', report)
            if runtime is not None:
                if not entered_acquisition:
                    report['driver_healthy'] = runtime.driver.healthy
                    report['quarantined_buffers'] = runtime.driver.quarantined_buffer_count
                    try:
                        report['diagnostics'] = runtime.driver.diagnostics()
                    except BaseException as exc:
                        report['diagnostics_error'] = repr(exc)
                    quiet_save(output / 'completion.json', report)
                from run_integrated_board import retain_until_quiescent
                retain_until_quiescent(runtime, output / 'completion.json', report)
        finally:
            if owner_file is not None:
                owner_file.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=DEFAULT_PROJECT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--client-date')
    parser.add_argument('--roi', type=int, nargs=4, default=(175, 340, 400, 400))
    parser.add_argument('--exposure', type=int, default=5)
    parser.add_argument('--max-angle', type=int, default=180)
    parser.add_argument('--run-board', action='store_true')
    parser.add_argument('--button-release', type=Path,
                        help='Explicit separately versioned button overlay; never downloaded here')
    args = parser.parse_args()
    project = args.project.resolve()
    modules, artifacts, plan = inspect(project, args.max_angle, tuple(args.roi), args.exposure,
                                       button_release=args.button_release)
    if not args.run_board:
        print(json.dumps({'hardware_accessed': False, 'artifacts': artifacts, 'plan': plan,
                          'roi': args.roi, 'exposure': args.exposure}, ensure_ascii=False, indent=2))
        return 0
    if not args.output or not args.client_date:
        parser.error('--run-board requires --output and --client-date')
    result = execute_board(project, args.output.resolve(), args.client_date, tuple(args.roi),
                           args.exposure, modules, artifacts, plan)
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
