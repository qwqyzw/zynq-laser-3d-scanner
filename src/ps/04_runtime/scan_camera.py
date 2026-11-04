"""Bounded camera process for Linux/PYNQ. Import performs no hardware access."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time


class CameraError(RuntimeError):pass


class BoundedCamera:
    def __init__(self,device='/dev/video0',startup_timeout_s=15,capture_timeout_s=8,*,_worker_script=None):
        if not 1<=startup_timeout_s<=30 or not 1<=capture_timeout_s<=20:
            raise ValueError('Invalid camera deadlines')
        self.device=device;self.startup_timeout=startup_timeout_s;self.capture_timeout=capture_timeout_s
        self.proc=None;self.selector=None;self.log=None;self.buffer=b'';self.sequence=0
        self.failed=False;self.cleanup={};self.mode=None
        self.closed=False
        self.worker_script=Path(_worker_script) if _worker_script else Path(__file__).with_name('scan_camera_worker.py')

    def _receive(self,timeout,expected_id):
        deadline=time.monotonic()+timeout
        while True:
            if b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1)
                try:message=json.loads(line)
                except Exception as exc:raise CameraError('Invalid worker JSON') from exc
                if message.get('id')!=expected_id:raise CameraError('Camera response ID mismatch')
                if not message.get('ok'):raise CameraError(str(message))
                return message
            remaining=deadline-time.monotonic()
            if remaining<=0 or not self.selector.select(max(0,remaining)):
                raise CameraError('Camera worker deadline exceeded')
            data=os.read(self.proc.stdout.fileno(),65536)
            if not data:raise CameraError('Camera worker exited before response')
            self.buffer+=data
            if len(self.buffer)>262144:raise CameraError('Oversized camera response')

    def _request(self,action,timeout,**data):
        if self.failed or self.proc is None:raise CameraError('Camera is not ready')
        self.sequence+=1
        try:
            payload=json.dumps(dict(action=action,id=self.sequence,**data)).encode()+b'\n'
            self.proc.stdin.write(payload);self.proc.stdin.flush()
            return self._receive(timeout,self.sequence)
        except BaseException:
            self.failed=True
            raise

    def start(self,scan_root):
        if self.proc is not None:raise CameraError('Camera already started')
        root=Path(scan_root).resolve();self.log=(root/'camera_stderr.log').open('wb')
        config={'root':str(root),'device':self.device}
        worker=self.worker_script
        try:
            self.proc=subprocess.Popen([sys.executable,'-u',str(worker),json.dumps(config)],
                                       stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,bufsize=0)
            self.selector=selectors.DefaultSelector();self.selector.register(self.proc.stdout,selectors.EVENT_READ)
            ready=self._receive(self.startup_timeout,0)
            if ready.get('event')!='ready':raise CameraError('Missing camera ready event')
            self.mode=ready['mode'];return ready
        except BaseException:
            self.failed=True
            raise

    def capture(self,folder):
        response=self._request('capture',self.capture_timeout,folder=str(Path(folder).resolve()))
        if response.get('event')!='captured' or len(response.get('images',[]))!=3:
            raise CameraError('Incomplete image batch')
        return response

    def close(self):
        if self.closed:return self.cleanup
        if self.proc is None:
            if self.log:self.log.close()
            self.cleanup={'camera_release_confirmed':False,'worker_started':False}
            return self.cleanup
        released=False;close_error=None
        try:
            if not self.failed and self.proc.poll() is None:
                response=self._request('close',3)
                released=response.get('camera_release_confirmed',False)
        except BaseException as exc:close_error=repr(exc)
        finally:
            if self.proc.poll() is None:
                try:self.proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:self.proc.terminate()
                    except ProcessLookupError:pass
                    try:self.proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:self.proc.kill()
                        except ProcessLookupError:pass
                        try:self.proc.wait(timeout=1)
                        except subprocess.TimeoutExpired:pass
            self.cleanup={'camera_release_confirmed':released,'worker_pid':self.proc.pid,
                          'worker_exited':self.proc.poll() is not None,'return_code':self.proc.poll(),
                          'close_error':close_error}
            if self.selector:self.selector.close()
            for stream in (self.proc.stdin,self.proc.stdout):
                if stream:stream.close()
            if self.log:self.log.close()
            self.closed=True
        if not released or self.proc.poll() is None:
            raise CameraError('Camera normal release unconfirmed; do not automatically retry')
        return self.cleanup
