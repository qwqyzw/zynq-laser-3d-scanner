"""Local read-only status/result display. No motion, camera, PL or XYZ math."""
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,unquote
import hashlib,json,re,sys,time,threading,mimetypes,argparse,uuid
HERE=Path(__file__).resolve().parent;P=HERE.parent
RECEIVED=P/'results/button_scan_received';REMOTE='/home/xilinx/jupyter_notebooks/button_scan'
LOCK=threading.Lock();SNAPSHOT={'connected':False,'message':'正在连接开发板','status':{},'latest':{}}

def result_identity(status,record):
 if record.get('state')!='ready' or record.get('discarded'):return None
 sid=record.get('session_id','');rid=record.get('run_id','')
 if not re.fullmatch('[0-9a-f]{32}',sid) or not re.fullmatch('[0-9a-f]{32}',rid):return None
 if status.get('child_pid') is not None or status.get('state') in ['starting','acquiring','stop_requested','recovery_required','button_pressed']:return None
 if status.get('run_id') not in [None,rid]:return None
 return sid+'_'+rid

def read_remote(s,path,maximum=200000):
 with s.open(path,'rb') as f:data=f.read(maximum+1)
 if len(data)>maximum:raise ValueError('Remote record exceeds expected size')
 return data

def fetch_review(s,record,directory):
 rid=record['run_id'];expected='runs/'+rid+'/model/pointcloud_review'
 if record.get('review_relative')!=expected:return
 data=read_remote(s,REMOTE+'/'+expected+'/review_manifest.json')
 if hashlib.sha256(data).hexdigest()!=record.get('review_manifest_sha256'):raise ValueError('Review manifest mismatch')
 manifest=json.loads(data);dest=directory/'diagnostics';dest.mkdir(exist_ok=True)
 if manifest.get('coordinate_producer')!='zynq' or manifest.get('surface_fitted') is not False:raise ValueError('Board measured diagnostics required')
 csv=directory/'points.csv'
 if hashlib.sha256(csv.read_bytes()).hexdigest()!=manifest['source_csv_sha256']:raise ValueError('Review refers to another cloud')
 for name in ['review.json','pointcloud_diagnostics.png']:
  row=manifest['files'][name]
  if not 0<row['bytes']<2000000:raise ValueError('Unexpected review size')
  path=dest/name
  if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']:continue
  raw=read_remote(s,REMOTE+'/'+expected+'/'+name,row['bytes'])
  if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Review file mismatch')
  temp=dest/(name+'.writing');temp.write_bytes(raw);temp.replace(path)

def fetch_surface(s,record,directory,preferences,model_status):
 base='/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020/'
 req=preferences.get('request_id','');shape=preferences.get('shape')
 if shape not in ['cone','cube','hex_prism','triangular_pyramid'] or not re.fullmatch('[0-9a-f]{32}',req):raise FileNotFoundError('Pointcloud-only display selected')
 expected='104_triangular_pyramid_display_20261103/algorithm_previews/'+record['run_id']+'/'+req
 if model_status.get('state')!='ready' or model_status.get('source_run_id')!=record['run_id'] or model_status.get('request_id')!=req or model_status.get('artifact_relative')!=expected:raise FileNotFoundError('Current selected model not ready')
 remote=base+expected;raw=read_remote(s,remote+'/artifact_manifest.json')
 manifest=json.loads(raw)
 if manifest.get('shape_type')!=shape or manifest.get('request_id')!=req:raise ValueError('Requested model shape differs')
 if manifest.get('source_run_id')!=record['run_id'] or manifest.get('coordinate_producer')!='zynq' or manifest.get('derived_estimate') is not True or manifest.get('current_when_generated') is not True:raise ValueError('Surface proposal identity differs')
 if manifest['source_csv_sha256']!=hashlib.sha256((directory/'points.csv').read_bytes()).hexdigest():raise ValueError('Surface proposal input differs')
 allowed={'ESTIMATED_UPPER_SURFACE.ply','denoised_points.ply','measured_points.ply','model_viewer.html','model_metadata.json'}
 if set(manifest['files'])!=allowed:raise ValueError('Unexpected proposal outputs')
 dest=directory/'surface_proposal';dest.mkdir(exist_ok=True)
 for name,row in manifest['files'].items():
  if not 0<row['bytes']<4000000:raise ValueError('Proposal file size rejected')
  path=dest/name
  if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']:continue
  data=read_remote(s,remote+'/'+name,row['bytes'])
  if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Proposal SHA mismatch')
  temp=dest/(name+'.writing');temp.write_bytes(data);temp.replace(path)
 (dest/'artifact_manifest.json').write_bytes(raw)
 meta=json.loads((dest/'model_metadata.json').read_text(encoding='utf-8'));meta['display_manifest_revision']=hashlib.sha256(raw).hexdigest()
 return meta

def poll_board():
 global SNAPSHOT
 sys.path.insert(0,str(P/'results/button_scan_20261020'));import board_ops
 sys.path.insert(0,str(P/'61_current_axis_laser_release_20261024'));from pc_receiver_cancel import verify_export
 while True:
  c=None
  try:
   c=board_ops.connect();s=c.open_sftp();status=json.loads(read_remote(s,REMOTE+'/status.json'));latest=json.loads(read_remote(s,REMOTE+'/latest_result.json'))
   model_base='/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020/104_triangular_pyramid_display_20261103/'
   try:preferences=json.loads(read_remote(s,model_base+'preferences.json'));model_status=json.loads(read_remote(s,model_base+'model_status.json'))
   except (OSError,ValueError):preferences={'shape':'pointcloud'};model_status={}
   data={'connected':True,'updated_epoch':time.time(),'status':status,'latest':latest,'result_identity':None,'review_available':False,'model_preferences':preferences,'model_status':model_status}
   identity=result_identity(status,latest)
   if identity:
    try:
     final_trial=json.loads(read_remote(s,REMOTE+'/runs/'+latest['run_id']+'/trial.json',1000000))
     if final_trial.get('elapsed_s') is not None:data['status']={**status,'elapsed_seconds':final_trial['elapsed_s']}
     selection=final_trial.get('foreground_selection')
     if selection and final_trial.get('run_id')==latest['run_id']:
      data['acquisition_selection']={k:selection[k] for k in ['raw_candidates','foreground_points','background_candidates_excluded','central_baseline_points','accepted_extra_measured_points'] if k in selection}
    except (OSError,ValueError):pass
    directory=RECEIVED/identity/'viewer'
    if directory.is_dir():
     verify_export(directory,latest)
     data['result_identity']=identity
     try:fetch_review(s,latest,directory)
     except (OSError,ValueError,KeyError) as error:data['review_note']=str(error)
     data['review_available']=(directory/'diagnostics/pointcloud_diagnostics.png').is_file()
     if (directory/'diagnostics/review.json').is_file():data['review']=json.loads((directory/'diagnostics/review.json').read_text(encoding='utf-8'))
     try:data['surface_proposal']=fetch_surface(s,latest,directory,preferences,model_status)
     except (OSError,ValueError,KeyError):pass
   with LOCK:SNAPSHOT=data
  except Exception as error:
   with LOCK:SNAPSHOT={**SNAPSHOT,'connected':False,'message':'开发板连接或结果校验暂不可用','result_identity':None}
  finally:
   if c:c.close()
  time.sleep(5)

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  if self.path!='/model-shape':return self.send(b'Not found','text/plain',404)
  origin=self.headers.get('Origin')
  if origin and origin!='http://127.0.0.1:8891':return self.send(b'Origin rejected','text/plain',403)
  c=None
  try:
   n=int(self.headers.get('Content-Length','0'))
   if not 0<n<2000:raise ValueError('Invalid request size')
   data=json.loads(self.rfile.read(n));shape=data.get('shape')
   if set(data)!={'shape'} or shape not in ['pointcloud','cone','cube','hex_prism','triangular_pyramid']:raise ValueError('Choose a supported object type')
   pref={'shape':shape,'request_id':uuid.uuid4().hex,'client_date':'2026-11-03','explicit_user_shape_selection':True,'applies_to_current_and_next_completed_scans':True}
   import board_ops
   c=board_ops.connect();s=c.open_sftp();remote='/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020/104_triangular_pyramid_display_20261103/preferences.json'
   temp=remote+'.'+pref['request_id']+'.writing'
   with s.open(temp,'wb') as f:f.write(json.dumps(pref,ensure_ascii=False).encode('utf-8'))
   s.posix_rename(temp,remote)
   self.send(json.dumps({'accepted':True,**pref},ensure_ascii=False).encode('utf-8'))
  except Exception:self.send(json.dumps({'accepted':False,'error':'保存失败，请检查开发板连接后重试。'},ensure_ascii=False).encode('utf-8'),code=503)
  finally:
   if c:c.close()
 def send(self,data,mime='application/json; charset=utf-8',code=200):
  self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_GET(self):
  path=unquote(urlsplit(self.path).path)
  with LOCK:snapshot=dict(SNAPSHOT)
  if path=='/':return self.send((HERE/'dashboard.html').read_bytes(),'text/html; charset=utf-8')
  if path=='/status.json':return self.send(json.dumps(snapshot,ensure_ascii=False).encode('utf-8'))
  if path.startswith('/result/'):
   identity=snapshot.get('result_identity');name=path[len('/result/'):]
   if not snapshot.get('connected') or not identity:return self.send(b'No current completed result','text/plain',404)
   directory=RECEIVED/identity/'viewer'
   try:
    manifest=json.loads((directory/'export_manifest.json').read_text(encoding='utf-8'));allowed=set(manifest['outputs'])|{'diagnostics/pointcloud_diagnostics.png'}
    if snapshot.get('surface_proposal'):
     proposal=json.loads((directory/'surface_proposal/artifact_manifest.json').read_text(encoding='utf-8'));allowed|={'surface_proposal/'+n for n in proposal['files']}
    target=(directory/name).resolve()
    if name not in allowed or not target.is_relative_to(directory.resolve()) or target.is_symlink():raise ValueError('Not an exported display file')
    return self.send(target.read_bytes(),mimetypes.guess_type(name)[0] or 'application/octet-stream')
   except (OSError,ValueError):return self.send(b'Result unavailable','text/plain',404)
  self.send(b'Not found','text/plain',404)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8891);args=parser.parse_args()
 server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
 threading.Thread(target=poll_board,daemon=True).start()
 print('Read-only scanner display at http://127.0.0.1:'+str(args.port)+'/',flush=True);server.serve_forever()
if __name__=='__main__':main()
