"""Step/settle/capture orchestration. No hardware access on import."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time


def validate_angles(angles, *, max_angle_deg=45, max_points=10):
    if not math.isfinite(max_angle_deg) or not 0<max_angle_deg<=90 or type(max_points) is not int or not 1<=max_points<=20:
        raise ValueError('Invalid scan envelope; maximum90deg and20points')
    angles=tuple(float(a) for a in angles)
    if not 1<=len(angles)<=max_points or angles[0]!=0 or any(not math.isfinite(a) or abs(a)>max_angle_deg for a in angles):
        raise ValueError('First angle must be0; angles and count must fit declared scan envelope')
    if any(abs(b-a)>30 for a,b in zip(angles,angles[1:])):
        raise ValueError('Adjacent requested angles may differ by at most30deg')
    return angles


def save_json(path,data):
    path=Path(path);temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def run_scan(motor,camera,output_dir,angles=(0,10,20,30,40),*,
             client_session_date=None,laser_state_reported='unverified',
             calibration_files=(),simulation=False,progress=print,
             max_angle_deg=45,max_points=10,preflight_capture=False,
             reuse_initial_settle=False):
    angles=validate_angles(angles,max_angle_deg=max_angle_deg,max_points=max_points)
    if laser_state_reported not in ('on','off','unverified'):
        raise ValueError('Laser state must be an operator label')
    root=Path(output_dir).resolve();root.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    report={'status':'starting','simulation':bool(simulation),'requested_angles_deg':angles,
            'client_session_date':client_session_date,
            'device_wall_time_unverified':datetime.now(timezone.utc).isoformat(),
            'points':[],'motor_turns_per_table_turn':1.0,'direct_coupling_user_confirmed':True,
            'scan_envelope':{'max_angle_deg':max_angle_deg,'max_points':max_points,'max_adjacent_deg':30},
            'independent_turntable_angle_accuracy_verified':False,
            'laser_state_reported':laser_state_reported,'image_laser_state_verified':False,
            'geometry_used_for_reconstruction':False,'calibration_files':[],
            'output_dir':str(root),'cleanup':{}}

    def event(kind,**data):
        with (root/'events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'event':kind,'elapsed_s':time.monotonic()-started,**data},ensure_ascii=False)+'\n')

    def checkpoint():save_json(root/'session.json',report)

    def verify_images(batch):
        if len(batch.get('images',[]))!=3:raise RuntimeError('Need three images per point')
        for item in batch['images']:
            image=Path(item['file']).resolve()
            if not image.is_relative_to(root) or not image.is_file():
                raise RuntimeError('Missing image or image path outside session')
            if hashlib.sha256(image.read_bytes()).hexdigest()!=item['sha256']:
                raise RuntimeError('Saved image hash mismatch')

    try:
        event('starting');checkpoint()
        for p in calibration_files:
            path=Path(p).resolve()
            report['calibration_files'].append({'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        report['camera_ready']=camera.start(root);event('camera_ready')
        if preflight_capture:
            # A failed current-scene quality gate must precede motor preparation,
            # which may enable the drive. Retain the actual preflight images.
            report['preflight_capture']=camera.capture(root/'preflight'/'images')
            verify_images(report['preflight_capture'])
            event('preflight_capture_passed');checkpoint()
        report['motor_ready']=motor.prepare();event('motor_ready')
        origin=report['motor_ready']['initial_position_deg']
        if not math.isfinite(origin):raise RuntimeError('Invalid motor origin')
        report['motor_origin_deg']=origin
        report['status']='running';checkpoint()
        for index,angle in enumerate(angles):
            point={'index':index,'requested_table_relative_deg':angle,'target_motor_deg':origin+angle,
                   'status':'moving'}
            report['points'].append(point);checkpoint();event('move_start',index=index,angle=angle)
            if index==0 and reuse_initial_settle:
                # prepare() already established this exact origin and dwell.
                # Avoid an extra one-pulse correction due to encoder noise.
                moved={'motor_position_deg':origin,
                       'settled':report['motor_ready']['initial_settle'],
                       'initial_settle_reused':True,
                       'motion_command_required':False}
            else:
                moved=motor.move_to(origin+angle,rpm=1)
            measured=moved['motor_position_deg']
            if not math.isfinite(measured):raise RuntimeError('Invalid settled motor position')
            point.update(move=moved,motor_position_before_capture_deg=measured,status='capturing')
            point['table_relative_deg_from_motor_feedback']=measured-origin
            event('settled',index=index,motor_position_deg=measured);checkpoint()
            point_dir=root/('point_%02d'%index);point_dir.mkdir(exist_ok=False)
            batch=camera.capture(point_dir/'images');verify_images(batch)
            after=motor.position()['motor_position_deg']
            if not math.isfinite(after):raise RuntimeError('Invalid post-capture position')
            point.update(capture=batch,motor_position_after_capture_deg=after,
                         position_change_during_capture_deg=after-measured)
            if abs(after-measured)>.1:raise RuntimeError('Motor position changed during capture')
            point['status']='completed';save_json(point_dir/'point.json',point)
            event('point_saved',index=index,actual_relative_deg=measured-origin);checkpoint()
            progress('已保存 %d/%d：请求%.2f°，反馈相对角度%.3f°，3张原图'%
                     (index+1,len(angles),angle,measured-origin))
        report['status']='completed'
    except BaseException as exc:
        report['status']='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed'
        report['error']=type(exc).__name__+': '+str(exc)
        if hasattr(exc,'samples'):report['failed_settle_samples']=exc.samples
        if report['points'] and report['points'][-1]['status']!='completed':
            report['points'][-1]['status']='failed'
        event('failed',error=report['error'])
    finally:
        # Stop first, before waiting on a failing camera shutdown.
        try:
            report['cleanup']['motor_stop']=motor.stop()
            report['cleanup']['motor_stop_confirmed']=True
        except BaseException as exc:
            report['cleanup']['motor_stop_confirmed']=False
            report['cleanup']['motor_stop_error']=repr(exc)
        try:report['cleanup']['camera']=camera.close()
        except BaseException as exc:
            report['cleanup']['camera']=getattr(camera,'cleanup',{})
            report['cleanup']['camera_close_error']=repr(exc)
        try:motor.close();report['cleanup']['motor_client_closed']=True
        except BaseException as exc:report['cleanup']['motor_close_error']=repr(exc)
        if not report['cleanup'].get('motor_stop_confirmed') or report['cleanup'].get('camera_close_error') or report['cleanup'].get('motor_close_error'):
            if report['status']=='completed':report['status']='cleanup_failed'
        report['motion_log']=getattr(motor,'motion_log',[])
        report['read_transactions']=getattr(motor,'transactions',[])
        report['elapsed_s']=time.monotonic()-started
        report['acquisition_pipeline_completed']=report['status']=='completed'
        report['real_hardware_pipeline_verified']=(report['status']=='completed' and not simulation
            and report.get('motor_ready',{}).get('backend') in
                ('hardware_pynq_uart','hardware_pynq_uartlite')
            and all(p.get('capture',{}).get('source')=='hardware_camera' for p in report['points']))
        event('closed',status=report['status']);checkpoint()
    return report


def run_hardware_scan(output_dir,**options):
    """Explicit hardware entry; caller must have prepared the physical scene."""
    validate_angles(options.get('angles',(0,10,20,30,40)),max_angle_deg=options.get('max_angle_deg',45),max_points=options.get('max_points',10))
    if Path(output_dir).exists():raise FileExistsError('Use a new session directory')
    from pynq.overlays.base import BaseOverlay
    from motor_position import ArduinoAngleController
    from scan_camera import BoundedCamera
    overlay=BaseOverlay('base.bit')
    motor=ArduinoAngleController(overlay,address=1)
    try:return run_scan(motor,BoundedCamera(),output_dir,**options)
    finally:
        if not motor.closed:motor.close()
