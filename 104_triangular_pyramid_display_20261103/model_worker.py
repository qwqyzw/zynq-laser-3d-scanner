"""File-only Zynq service. Never opens camera/UART/GPIO/DMA or changes latest."""
from pathlib import Path
import sys,json,csv,time,hashlib,platform,shutil,re
import numpy as np
HERE=Path(__file__).resolve().parent;P=HERE.parent;B=Path('/home/xilinx/jupyter_notebooks/button_scan')
sys.path[:0]=[str(HERE),str(P/'87_hex_prism_surface_20261024'),str(P/'86_integer_reference_fix_20261024'),str(P/'09_tools')]
from shape_models import estimate,LABELS
def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name(p.name+'.writing');t.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');t.replace(p)
def current(rid,request_id):
 r=load(B/'latest_result.json');s=load(B/'status.json');pref=load(HERE/'preferences.json')
 return r.get('run_id')==rid and r.get('state')=='ready' and not r.get('discarded') and s.get('child_pid') is None and s.get('state') not in ['starting','acquiring','stop_requested','recovery_required','button_pressed'] and pref.get('request_id')==request_id and pref.get('wait_for_new_run_after')!=rid
def ply(path,points,faces=None):
 with Path(path).open('w') as f:
  f.write('ply\nformat ascii 1.0\ncomment derived estimate; original CSV retained on Zynq\nelement vertex %d\nproperty double x\nproperty double y\nproperty double z\n'%len(points))
  if faces is not None:f.write('element face %d\nproperty list uchar int vertex_indices\n'%len(faces))
  f.write('end_header\n')
  for p in points:f.write(' '.join(format(float(x),'.17g') for x in p)+'\n')
  if faces is not None:
   for face in faces:f.write('3 '+' '.join(str(int(x)) for x in face)+'\n')
def build(pref,record):
 from cleanup86 import inspect_cleanup
 from board_pointcloud_export import board_identity
 if not board_identity()['is_linux_arm_zynq_or_pynq']:raise ValueError('Real model fitting must run on Zynq')
 rid=record['run_id'];req=pref['request_id'];shape=pref['shape'];root=B/'runs'/rid
 if not re.fullmatch('[0-9a-f]{32}',rid) or not re.fullmatch('[0-9a-f]{32}',req) or shape not in LABELS:raise ValueError('Invalid model request')
 if not current(rid,req):raise ValueError('Source is no longer current')
 inspect_cleanup(root,'06e6c6cc0167c16832edf85a0470499f7744062aeca473cf8e80f015a88d60b5')
 source=root/'model/object_points.csv';before=sha(source);export=load(root/'model/viewer/export_manifest.json')
 if sha(root/'model/viewer/export_manifest.json')!=record['export_manifest_sha256'] or export['outputs']['points.csv']['sha256']!=before:raise ValueError('Current measured CSV provenance differs')
 out=HERE/'algorithm_previews'/rid/req
 if (out/'artifact_manifest.json').exists():
  meta=load(out/'model_metadata.json')
  if meta['source_sha256']==before and meta['shape_type']==shape:return meta,out
  raise ValueError('Existing request cannot be reused with different input')
 if out.exists():raise ValueError('Incomplete model output needs a new request')
 rows=list(csv.DictReader(source.open()));points=np.array([[float(r[k]) for k in ['x_mm','y_mm','z_mm']] for r in rows]);groups=np.array([int(r['point_index'])%36 for r in rows]);started=time.monotonic();m=estimate(points,groups,shape)
 if sha(source)!=before or not current(rid,req):raise ValueError('Input changed during fitting')
 out.mkdir(parents=True);ply(out/'ESTIMATED_UPPER_SURFACE.ply',m['vertices'],m['triangles']);ply(out/'denoised_points.ply',m['cleaned']);ply(out/'measured_points.ply',points)
 stats=m['stats'];stats['foreground_selection']={'foreground_points':len(points),'raw_candidates':len(points),'background_candidates_excluded':0,'height_range_mm':[min(float(r['height_above_reference_mm']) for r in rows),max(float(r['height_above_reference_mm']) for r in rows)],'empty_unique_angles_deg':[i*10 for i in range(36) if i not in set(groups)]}
 data={'vertices':m['vertices'].tolist(),'faces':m['triangles'].tolist(),'points':m['cleaned'].tolist(),'weak':m['weak_faces'].tolist(),'stats':stats,'source_run':rid}
 html=(HERE/'viewer_template.html').read_text(encoding='utf-8').replace('__MODEL_DATA__',json.dumps(data)).replace('__SHAPE_LABEL__',LABELS[shape]).replace('__ASSUMPTION__',stats['assumption'])
 (out/'model_viewer.html').write_text(html,encoding='utf-8')
 meta={'source_run_id':rid,'source_sha256':before,'coordinate_producer':'zynq','derived_estimate':True,'shape_completion':True,'shape_type':shape,'shape_label':LABELS[shape],'request_id':req,'historical_algorithm_preview':False,'current_when_generated':True,'raw_points_modified':False,'latest_result_modified':False,'processing_s':time.monotonic()-started,'statistics':stats,'operator_request':pref,'source_scripts':{n:sha(HERE/n) for n in ['shape_models.py','model_worker.py','viewer_template.html','triangular_pyramid.py']}}
 save(out/'model_metadata.json',meta)
 save(out/'artifact_manifest.json',{'source_run_id':rid,'source_csv_sha256':before,'coordinate_producer':'zynq','derived_estimate':True,'shape_completion':True,'shape_type':shape,'request_id':req,'current_when_generated':True,'files':{f.name:{'bytes':f.stat().st_size,'sha256':sha(f)} for f in out.iterdir() if f.is_file()}})
 if not current(rid,req):raise ValueError('Model is no longer current')
 return meta,out
def _step():
 pref=load(HERE/'preferences.json');shape=pref.get('shape','pointcloud');record=load(B/'latest_result.json');rid=record.get('run_id');req=pref.get('request_id')
 if record.get('discarded') and rid and re.fullmatch('[0-9a-f]{32}',rid):
  target=HERE/'algorithm_previews'/rid
  if target.exists() and not target.is_symlink() and target.resolve().parent==(HERE/'algorithm_previews').resolve():shutil.rmtree(target)
 if pref.get('wait_for_new_run_after') and pref.get('wait_for_new_run_after')==rid:
  save(HERE/'model_status.json',{'state':'waiting_for_new_scan','shape_type':shape,'request_id':req});return
 if shape=='pointcloud':save(HERE/'model_status.json',{'state':'pointcloud','shape_type':shape,'request_id':req});return
 if record.get('state')!='ready' or not current(rid,req):return
 previous=load(HERE/'model_status.json') if (HERE/'model_status.json').exists() else {}
 if previous.get('source_run_id')==rid and previous.get('request_id')==req and previous.get('state') in ['ready','failed']:return
 state={'state':'processing','source_run_id':rid,'request_id':req,'shape_type':shape,'shape_label':LABELS.get(shape,shape),'coordinate_producer':'zynq'};save(HERE/'model_status.json',state)
 try:
  meta,out=build(pref,record)
  if current(rid,req):save(HERE/'model_status.json',{**state,'state':'ready','artifact_relative':out.relative_to(P).as_posix(),'processing_s':meta['processing_s'],'statistics':meta['statistics']})
 except Exception as e:
  save(HERE/'model_status.json',{**state,'state':'failed','error':str(e),'measured_cloud_preserved':True})
def step():
 import fcntl
 with open('/tmp/zynq_multi_shape_model.lock','a') as owner:
  try:fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  return _step()
def main():
 if '--manifest-sha256' in sys.argv:
  pin=sys.argv[sys.argv.index('--manifest-sha256')+1]
  if sha(HERE/'manifest.json')!=pin:raise ValueError('Multi-shape manifest changed')
  for name,digest in load(HERE/'manifest.json')['files'].items():
   if Path(name).name!=name or sha(HERE/name)!=digest:raise ValueError('Multi-shape source changed')
 if '--once' in sys.argv:step();return
 while True:
  try:step()
  except (OSError,ValueError,KeyError):pass
  time.sleep(2)
if __name__=='__main__':main()
