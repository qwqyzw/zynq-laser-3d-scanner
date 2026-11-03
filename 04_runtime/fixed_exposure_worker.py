
import cv2,os,fcntl,struct,time,json,sys,hashlib
from pathlib import Path
root=Path(sys.argv[1]);exposure=int(sys.argv[2]);scene=sys.argv[3]
cap=None;fd=None;original={};frames=[];restore_errors=[];status='starting';changed=False;bright_frames=[]
ids={'ae':0x9a0901,'exp':0x9a0902,'awb':0x98090c,'wb':0x98091a,'gain':0x980913}
def read(name):
    b=bytearray(struct.pack('=Ii',ids[name],0));fcntl.ioctl(fd,0xC008561B,b,True)
    return struct.unpack('=Ii',b)[1]
def write(name,value):
    fcntl.ioctl(fd,0xC008561C,bytearray(struct.pack('=Ii',ids[name],value)),True)
    if read(name)!=value:raise RuntimeError('参数读回不一致：'+name)
def warm(seconds):
    start=time.monotonic()
    while time.monotonic()-start<seconds:
        ok,img=cap.read()
        if not ok or img is None:raise RuntimeError('连续取图失败')
def save_bright(phase):
    for i in range(3):
        warm(.35)
        ok,img=cap.read()
        if not ok or img is None or img.shape!=(480,640,3):raise RuntimeError('定位照取图失败')
        name=phase+'_%02d.png'%i
        if not cv2.imwrite(str(root/name),img):raise RuntimeError('定位照保存失败')
        bright_frames.append({'file':name,'phase':phase,'controls_readback':{k:read(k) for k in ids},
            'monotonic_s':time.monotonic(),'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest()})
try:
    print('打开相机；只修改拍摄亮度，不写颜色控制。',flush=True)
    cap=cv2.VideoCapture('/dev/video0',cv2.CAP_V4L2)
    if not cap.isOpened():raise RuntimeError('相机未打开')
    cap.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,640);cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480);cap.set(cv2.CAP_PROP_FPS,10)
    word=int(cap.get(cv2.CAP_PROP_FOURCC))
    mode={"fourcc":''.join(chr((word>>(8*i))&255) for i in range(4)),"width":cap.get(cv2.CAP_PROP_FRAME_WIDTH),"height":cap.get(cv2.CAP_PROP_FRAME_HEIGHT),"fps_readback":cap.get(cv2.CAP_PROP_FPS)}
    (root/'mode.json').write_text(json.dumps(mode,indent=2))
    if mode['fourcc']!='MJPG' or mode['width']!=640 or mode['height']!=480 or abs(mode['fps_readback']-10)>.1:raise RuntimeError('取图模式不一致')
    warm(3)
    fd=os.open('/dev/video0',os.O_RDWR|os.O_NONBLOCK)
    original={name:read(name) for name in ids}
    save_bright('bright_before')
    for name,value in [('ae',1),('exp',exposure)]:
        q=bytearray(struct.pack('=II32siiiiIII',ids[name],0,b'',0,0,0,0,0,0,0))
        fcntl.ioctl(fd,0xC0445624,q,True); info=struct.unpack('=II32siiiiIII',q)
        if info[5]<=0 or not info[3]<=value<=info[4] or (value-info[3])%info[5]:
            raise RuntimeError('不支持的相机参数：'+name)
    changed=True;write('ae',1);write('exp',exposure)
    print('拍摄亮度已固定：'+str(exposure),flush=True)
    warm(3)
    for index in range(3):
        warm(1)
        ok,img=cap.read()
        if not ok or img is None or img.shape!=(480,640,3):raise RuntimeError('原图尺寸或取图错误')
        controls={name:read(name) for name in ids}
        if controls['ae']!=1 or controls['exp']!=exposure:raise RuntimeError('亮度设置变化')
        name='raw_%02d.png'%index
        if not cv2.imwrite(str(root/name),img):raise RuntimeError('照片保存失败')
        frames.append({'file':name,'controls_readback':controls,
            'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest()})
        print('已拍摄 %d/3'%(index+1),flush=True)
    status='completed'
except BaseException as exc:
    status='failed';print('拍摄停止：'+repr(exc),flush=True)
finally:
    # Restore while streaming. WB/gain are never written.
    if changed and 'ae' in original and 'exp' in original:
        try:
            write('ae',1);write('exp',original['exp']);write('ae',original['ae'])
        except Exception as exc:restore_errors.append(repr(exc))
    restored=False
    if fd is not None and len(original)==5 and not restore_errors:
        try:restored=read('ae')==original['ae'] and read('exp')==original['exp']
        except Exception as exc:restore_errors.append(repr(exc))
    if restored and status=='completed':
        try:
            warm(3);save_bright('bright_after')
        except Exception as exc:
            status='failed';restore_errors.append('bright_after: '+repr(exc))
    report={'bright_frames':bright_frames,'paired_image_purpose':'bright marks / fixed-exposure laser',
            'status':status,'scene_requested':scene,'scene_visual_confirmation':False,
            'exposure_trial':exposure,'original_controls':original,'frames':frames,
            'ae_exposure_restoration_verified':restored,'restore_errors':restore_errors,
            'white_balance_gain_written':False,'white_balance_frozen':False,
            'height_reference_accepted':False}
    (root/'capture.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if fd is not None:os.close(fd)
    if cap is not None:cap.release()
    report['camera_release_completed']=True
    (root/'capture.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('摄像头已释放；原亮度设置恢复：'+('已验证' if restored else '未确认'),flush=True)
