"""Camera-only RPC owner. No motor/DMA imports. Restore once before final exit."""
import cv2,fcntl,hashlib,json,os,signal,struct,sys,time,uuid
from pathlib import Path
cv2.setNumThreads(1)
root=Path(sys.argv[1]).resolve();exposure=int(sys.argv[2]);session=uuid.uuid4().hex
root.mkdir(parents=True,exist_ok=False)
ids={'ae':0x9a0901,'exp':0x9a0902,'awb':0x98090c,'wb':0x98091a,'gain':0x980913}
cap=fd=None;original={};changed=False;captures=0;closing=False;error=None;sequence=0;bright=[]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 temporary=p.with_name(p.name+'.writing');temporary.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False));temporary.replace(p)
def emit(v):print(json.dumps(v,ensure_ascii=False,allow_nan=False),flush=True)
def read(name):
 b=bytearray(struct.pack('=Ii',ids[name],0));fcntl.ioctl(fd,0xC008561B,b,True);return struct.unpack('=Ii',b)[1]
def write(name,value):
 fcntl.ioctl(fd,0xC008561C,bytearray(struct.pack('=Ii',ids[name],value)),True)
 if read(name)!=value:raise RuntimeError('Exposure readback differs: '+name)
def frame():
 ok,image=cap.read()
 if not ok or image is None or image.shape!=(480,640,3):raise RuntimeError('Physical camera read/mode failed')
 return image
def flush(seconds,minimum):
 started=time.monotonic();count=0
 while time.monotonic()-started<seconds or count<minimum:frame();count+=1
 return {'elapsed_s':time.monotonic()-started,'discarded_frames':count,'minimum_frames':minimum}
def photograph(folder,name):
 image=frame();now=time.monotonic();controls={k:read(k) for k in ids}
 if not cv2.imwrite(str(folder/name),image,[cv2.IMWRITE_PNG_COMPRESSION,1]):raise RuntimeError('Original PNG saving failed')
 return {'file':name,'monotonic_s':now,'controls_readback':controls,'sha256':sha(folder/name)}
def bright_images(phase):
 folder=root/phase;folder.mkdir()
 for i in range(3):
  flush(.25,2);row=photograph(folder,'bright_%02d.png'%i);bright.append({'phase':phase,**row})
def interrupted(signum,stack):raise KeyboardInterrupt('Camera RPC owner interrupted')
signal.signal(signal.SIGTERM,interrupted)
try:
 cap=cv2.VideoCapture('/dev/video0',cv2.CAP_V4L2)
 if not cap.isOpened():raise RuntimeError('Cannot open physical camera')
 cap.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))
 cap.set(cv2.CAP_PROP_FRAME_WIDTH,640);cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480);cap.set(cv2.CAP_PROP_FPS,10)
 word=int(cap.get(cv2.CAP_PROP_FOURCC));mode={'fourcc':''.join(chr(word>>(8*i)&255) for i in range(4)),
       'width':cap.get(cv2.CAP_PROP_FRAME_WIDTH),'height':cap.get(cv2.CAP_PROP_FRAME_HEIGHT),'fps_readback':cap.get(cv2.CAP_PROP_FPS)}
 if mode['fourcc']!='MJPG' or mode['width']!=640 or mode['height']!=480 or abs(mode['fps_readback']-10)>.1:raise RuntimeError('Camera mode differs')
 flush(3,12);fd=os.open('/dev/video0',os.O_RDWR|os.O_NONBLOCK);original={k:read(k) for k in ids}
 bright_images('bright_before_session')
 for name,value in [('ae',1),('exp',exposure)]:
  q=bytearray(struct.pack('=II32siiiiIII',ids[name],0,b'',0,0,0,0,0,0,0));fcntl.ioctl(fd,0xC0445624,q,True);info=struct.unpack('=II32siiiiIII',q)
  if info[5]<=0 or not info[3]<=value<=info[4] or (value-info[3])%info[5]:raise RuntimeError('Unsupported exposure setting')
 changed=True;write('ae',1);write('exp',exposure);initial_flush=flush(8,32)
 startup={'schema':'persistent_manual_exposure_v1','session_id':session,'mode':mode,'original_controls':original,
          'exposure':exposure,'manual_controls_verified':read('ae')==1 and read('exp')==exposure,
          'worker_sha256':sha(Path(__file__)),'startup_flush':initial_flush,'bright_before':bright.copy(),
          'white_balance_gain_written':False,'camera_open_count':1}
 save(root/'session_start.json',startup);emit({'ready':True,**startup})
 for line in sys.stdin:
  request=json.loads(line);sequence+=1
  if request.get('sequence')!=sequence:raise RuntimeError('RPC sequence changed')
  if request.get('operation')=='close':closing=True;break
  if request.get('operation')!='capture' or captures>=75:raise RuntimeError('Invalid/over-budget camera request')
  folder=Path(request['folder']).resolve()
  if not folder.is_relative_to(root.parent) or folder==root or root in folder.parents:raise RuntimeError('Capture directory escapes scan')
  folder.mkdir(parents=True,exist_ok=False);started=time.monotonic();discarded=flush(.1,6);frames=[]
  for i in range(3):
   row=photograph(folder,'raw_%02d.png'%i)
   if row['controls_readback']['ae']!=1 or row['controls_readback']['exp']!=exposure:raise RuntimeError('Manual exposure changed')
   frames.append(row)
  record={'schema':'persistent_manual_exposure_v1','status':'completed','session_id':session,
          'session_start_sha256':sha(root/'session_start.json'),'sequence':sequence,'exposure_trial':exposure,
          'frames':frames,'bright_frames':[],'original_controls':original,'white_balance_gain_written':False,
          'white_balance_frozen':False,'session_still_streaming':True,'camera_release_completed':False,
          'ae_exposure_restoration_verified':False,'restore_errors':[],'post_settle_flush':discarded,
          'capture_elapsed_s':time.monotonic()-started,'worker_sha256':startup['worker_sha256']}
  save(folder/'capture.json',record);captures+=1;emit({'captured':True,'sequence':sequence,'record':str(folder/'capture.json'),'session_id':session})
 if not closing:raise RuntimeError('Camera RPC input closed without explicit close')
except BaseException as exc:error=type(exc).__name__+': '+str(exc)
finally:
 restore_errors=[];restored=False;after={}
 if changed and fd is not None and original:
  try:write('ae',1);write('exp',original['exp']);write('ae',original['ae'])
  except BaseException as exc:restore_errors.append(type(exc).__name__+': '+str(exc))
 if fd is not None and original and not restore_errors:
  try:
   after={k:read(k) for k in ids};restored=after['ae']==original['ae'] and after['exp']==original['exp']
   if restored and closing and error is None:flush(3,12);bright_images('bright_after_session')
  except BaseException as exc:restore_errors.append(type(exc).__name__+': '+str(exc));restored=False
 if fd is not None:os.close(fd)
 if cap is not None:cap.release()
 receipt={'schema':'persistent_manual_exposure_v1','session_id':session,'captured_batches':captures,
          'camera_release_confirmed':True,'exposure_restoration_verified':restored,'restore_errors':restore_errors,
          'explicit_close_received':closing,'original_controls':original,'restored_controls':after,
          'white_balance_gain_written':False,'bright_images':bright,'error':error,'worker_sha256':sha(Path(__file__))}
 save(root/'session_close.json',receipt)
 emit({'closed':error is None and closing and restored and not restore_errors,'error':error,'receipt':str(root/'session_close.json')})
sys.exit(0 if closing and error is None and restored and not restore_errors else 1)
