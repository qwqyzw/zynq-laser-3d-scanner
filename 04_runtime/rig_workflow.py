"""Explicit operator entry points. No hardware action on import.

Pass an already chosen overlay; this module never downloads/switches one.
Small scans validate the current camera quality before preparing the motor.
"""
from pathlib import Path
import json
from datetime import date
from rotation_scan import run_scan,validate_angles
from scan_fixed_exposure import PerPoseExposureCamera
from scan_checked_exposure import StripeCheckedExposureCamera
from motor_position import ArduinoAngleController

def worker_source():return Path(__file__).with_name('fixed_exposure_worker.py').read_text()

def capture_only(output_dir,*,exposure=5):
    """Explicit Linux/PYNQ camera operation;3 laser and6 bright frames, no motor."""
    root=Path(output_dir).resolve();root.mkdir(parents=True,exist_ok=False)
    camera=PerPoseExposureCamera(worker_source(),exposure=exposure,timeout_s=40)
    try:
        camera.start(root);result=camera.capture(root/'images')
        (root/'capture_result.json').write_text(json.dumps(result,indent=2));return result
    finally:camera.close()

def run_small_scan(overlay,output_dir,roi,*,client_session_date,backend='PS',
                   pl_driver=None,verify_ps=True,angles=(0,5,0),exposure=5):
    """Explicit motion: three poses within10deg. Does NOT infer geometric phase."""
    if not isinstance(client_session_date,str) or date.fromisoformat(client_session_date).isoformat()!=client_session_date:
        raise ValueError('Supply actual client date as YYYY-MM-DD')
    validate_angles(angles,max_angle_deg=10,max_points=3)
    if backend not in ('PS','PL'):raise ValueError('Choose PS or PL explicitly')
    has_uartlite='axi_uartlite_0' in getattr(overlay,'ip_dict',{})
    if not has_uartlite and not hasattr(overlay,'iop_arduino'):
        raise RuntimeError('This overlay lacks old motor UART; finish integration before motion')
    if Path(output_dir).exists():raise FileExistsError('Use a fresh session directory')
    if backend=='PL':
        if pl_driver is None:raise ValueError('Pass the existing exclusive PL driver')
        from pl_scan_camera import PLStripeCheckedCamera
        camera=PLStripeCheckedCamera(worker_source(),roi,pl_driver,verify_ps=verify_ps,exposure=exposure)
    else:camera=StripeCheckedExposureCamera(worker_source(),roi,exposure=exposure,timeout_s=40)
    if has_uartlite:
        from motor_uartlite import UARTLiteAngleController
        motor=UARTLiteAngleController(overlay,address=1)
    else:
        motor=ArduinoAngleController(overlay,address=1)
    return run_scan(motor,camera,output_dir,angles=angles,client_session_date=client_session_date,
                    laser_state_reported='on',calibration_files=(),max_angle_deg=10,max_points=3,
                    preflight_capture=True,reuse_initial_settle=True)
