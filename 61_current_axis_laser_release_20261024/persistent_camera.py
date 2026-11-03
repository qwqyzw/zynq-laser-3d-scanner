"""Bounded RPC camera client. Camera child owns no motor/DMA resources."""
import hashlib,json,select,subprocess,sys,time
from pathlib import Path

class PersistentCamera:
 def __init__(self,source,*,exposure=5,timeout_s=40):
  compile(source,'persistent_camera_worker.py','exec')
  if exposure!=5 or not 1<=timeout_s<=40:raise ValueError('Explicit tested exposure/deadline required')
  self.source=source;self.exposure=exposure;self.timeout=timeout_s;self.root=None;self.proc=None
  self.reports=[];self.capture_blocked=False;self.uncertain=False;self.sequence=0;self.cleanup={};self.closed=False
 def _receive(self,timeout):
  ready,_,_=select.select([self.proc.stdout],[],[],timeout)
  if not ready:raise TimeoutError('Camera RPC response timed out; stop without retry')
  line=self.proc.stdout.readline()
  if not line:raise RuntimeError('Camera RPC owner exited')
  return json.loads(line)
 def _request(self,operation,**fields):
  if self.proc.poll() is not None:raise RuntimeError('Camera RPC owner already exited')
  self.sequence+=1;self.proc.stdin.write(json.dumps({'operation':operation,'sequence':self.sequence,**fields})+'\n');self.proc.stdin.flush()
  return self._receive(self.timeout)
 def start(self,root):
  if self.root is not None:raise RuntimeError('Camera session already started')
  self.root=Path(root).resolve();self.session_root=self.root/'camera_session';self.worker=self.root/'persistent_camera_worker.py'
  self.worker.write_text(self.source,encoding='utf-8');self.worker_hash=hashlib.sha256(self.worker.read_bytes()).hexdigest()
  self.log=open(self.root/'camera_session_stderr.log','w')
  self.proc=subprocess.Popen([sys.executable,'-B','-u',str(self.worker),str(self.session_root),str(self.exposure)],
     stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,bufsize=1)
  self.uncertain=True;ready=self._receive(self.timeout)
  if ready.get('ready') is not True or ready.get('worker_sha256')!=self.worker_hash or ready.get('manual_controls_verified') is not True:
   raise RuntimeError('Persistent camera startup not verified')
  self.session_id=ready['session_id'];self.uncertain=False
  return {'event':'persistent_camera_prepared','session_id':self.session_id,'worker_sha256':self.worker_hash,
          'exposure':self.exposure,'camera_open_count':1,'session_start_sha256':self._sha(self.session_root/'session_start.json')}
 @staticmethod
 def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
 def capture(self,folder):
  if self.root is None or self.uncertain or self.capture_blocked or self.closed:raise RuntimeError('Camera not ready')
  folder=Path(folder).resolve()
  if not folder.is_relative_to(self.root):raise ValueError('Capture escapes scan root')
  self.uncertain=True
  try:
   reply=self._request('capture',folder=str(folder))
   if reply.get('captured') is not True or reply['session_id']!=self.session_id:raise RuntimeError('Camera capture failed')
   record=json.loads((folder/'capture.json').read_text())
   if record['schema']!='persistent_manual_exposure_v1' or record['session_id']!=self.session_id or record['status']!='completed':raise RuntimeError('Capture identity changed')
   if len(record['frames'])!=3 or record['post_settle_flush']['discarded_frames']<6:raise RuntimeError('Missing fresh original frames')
   for i,row in enumerate(record['frames']):
    if row['file']!='raw_%02d.png'%i or self._sha(folder/row['file'])!=row['sha256']:raise RuntimeError('Original frame hash/order changed')
    if row['controls_readback']['ae']!=1 or row['controls_readback']['exp']!=self.exposure:raise RuntimeError('Exposure changed')
   self.reports.append(record);self.uncertain=False
   return {'source':'hardware_camera','images':[dict(row,file=str(folder/row['file'])) for row in record['frames']],
      'capture_record':str(folder/'capture.json'),'exposure':self.exposure,'exposure_restored':False,
      'camera_release_confirmed':False,'white_balance_frozen':False,'camera_session_id':self.session_id,
      'session_still_streaming':True,'capture_schema':'persistent_manual_exposure_v1'}
  except BaseException:self.capture_blocked=True;raise
 def close(self):
  if self.closed:return self.cleanup
  error=None
  try:
   if self.proc is not None and self.proc.poll() is None:
    reply=self._request('close')
    if reply.get('closed') is not True:raise RuntimeError('Camera close rejected: '+str(reply.get('error')))
    self.proc.wait(timeout=4)
  except BaseException as exc:error=exc
  if self.proc is not None and self.proc.poll() is None:
   self.proc.terminate()
   try:self.proc.wait(timeout=5)
   except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=2)
  receipt_path=self.session_root/'session_close.json'
  receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
  good=bool(receipt.get('camera_release_confirmed') is True and receipt.get('exposure_restoration_verified') is True
       and receipt.get('restore_errors')==[] and receipt.get('worker_sha256')==self.worker_hash
       and self.proc.poll() is not None)
  self.cleanup={'camera_release_confirmed':good,'exposure_restoration_verified':good,
     'capture_count':len(self.reports),'no_active_capture_process':self.proc is None or self.proc.poll() is not None,
     'capture_blocked_after_failure':self.capture_blocked,'session_close_sha256':self._sha(receipt_path) if receipt_path.exists() else None,
     'persistent_camera_session_closed':good}
  self.closed=True;self.log.close()
  if error is not None or not good:raise RuntimeError('Persistent camera shutdown not fully verified: '+str(error))
  return self.cleanup
