"""Private camera subprocess. Opens once; stdin/stdout carry JSON messages.

No camera, GUI or PYNQ access on import. Exposure/WB/gain are only read back.
"""
import hashlib
import json
import math
from pathlib import Path
import sys
import time


def emit(message):
    print(json.dumps(message,ensure_ascii=False),flush=True)


def control_readback(cap, key):
    value=float(cap.get(key))
    return value if math.isfinite(value) else None


def main(config):
    import cv2
    root=Path(config['root']).resolve();cap=None;error=None;request_id=0;released=False
    try:
        cap=cv2.VideoCapture(config.get('device','/dev/video0'),cv2.CAP_V4L2)
        if not cap.isOpened():raise RuntimeError('Camera not opened')
        settings=[(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG')),
                  (cv2.CAP_PROP_FRAME_WIDTH,640),(cv2.CAP_PROP_FRAME_HEIGHT,480),
                  (cv2.CAP_PROP_FPS,10)]
        set_results=[bool(cap.set(k,v)) for k,v in settings]
        word=int(cap.get(cv2.CAP_PROP_FOURCC))
        mode={'fourcc':''.join(chr((word>>(8*i))&255) for i in range(4)),
              'width':cap.get(cv2.CAP_PROP_FRAME_WIDTH),'height':cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
              'fps_readback':cap.get(cv2.CAP_PROP_FPS),'set_results':set_results,
              'exposure_readback':control_readback(cap,cv2.CAP_PROP_EXPOSURE),
              'auto_exposure_readback':control_readback(cap,cv2.CAP_PROP_AUTO_EXPOSURE),
              'gain_readback':control_readback(cap,cv2.CAP_PROP_GAIN),
              'white_balance_readback':control_readback(cap,cv2.CAP_PROP_WB_TEMPERATURE),
              'exposure_wb_gain_written':False,'fixed_photometric_settings_verified':False}
        if mode['fourcc']!='MJPG' or mode['width']!=640 or mode['height']!=480 or not math.isfinite(mode['fps_readback']) or abs(mode['fps_readback']-10)>.1:
            raise RuntimeError('Camera mode readback mismatch: '+repr(mode))
        emit({'ok':True,'event':'ready','id':0,'mode':mode})

        def read_frame():
            started=time.monotonic();ok,image=cap.read();finished=time.monotonic()
            if not ok or image is None or image.shape!=(480,640,3):
                raise RuntimeError('Invalid camera frame')
            return image,{'read_started_monotonic':started,'read_finished_monotonic':finished,
                          'sensor_exposure_timestamp_verified':False}

        for line in sys.stdin:
            request=json.loads(line);request_id=request['id']
            if request['action']=='close':break
            if request['action']!='capture':raise ValueError('Unsupported camera action')
            target=Path(request['folder']).resolve()
            if not target.is_relative_to(root) or target==root:
                raise ValueError('Capture folder must be inside this new scan directory')
            target.mkdir(parents=True,exist_ok=False)
            # Drain queued frames and spend real stream time after motor settling.
            begin=time.monotonic();discarded=0
            while discarded<6 or time.monotonic()-begin<.6:
                read_frame();discarded+=1
            images=[]
            for i in range(3):
                image,timing=read_frame();path=target/('raw_%02d.png'%i)
                if not cv2.imwrite(str(path),image):raise RuntimeError('Image write failed')
                images.append({'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                               'shape':list(image.shape),'mean_bgr':image.mean(axis=(0,1)).tolist(),**timing})
            batch_mode=dict(mode,exposure_readback=control_readback(cap,cv2.CAP_PROP_EXPOSURE),
                            auto_exposure_readback=control_readback(cap,cv2.CAP_PROP_AUTO_EXPOSURE),
                            gain_readback=control_readback(cap,cv2.CAP_PROP_GAIN),
                            white_balance_readback=control_readback(cap,cv2.CAP_PROP_WB_TEMPERATURE),
                            control_readback_time='after_batch')
            emit({'ok':True,'event':'captured','id':request_id,'images':images,
                  'discarded_frames':discarded,'mode':batch_mode,'source':'hardware_camera'})
    except BaseException as exc:
        error=repr(exc)
    finally:
        if cap is not None:
            try:cap.release();released=True
            except Exception as exc:error=error or repr(exc)
    emit({'ok':error is None,'event':'closed' if error is None else 'error',
          'id':request_id,'error':error,'camera_release_confirmed':released})
    return 0 if error is None else 1


if __name__=='__main__':
    raise SystemExit(main(json.loads(sys.argv[1])))
