from pathlib import Path
import json,hashlib,csv,sys,time,re
import numpy as np
from hex_model import estimate
HERE=Path(__file__).resolve().parent;P=HERE.parent;B=Path('/home/xilinx/jupyter_notebooks/button_scan')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def verify(pin):
 if sha(HERE/'manifest.json')!=pin:raise ValueError('Hex model manifest changed')
 data=json.loads((HERE/'manifest.json').read_text())
 for name,h in data['files'].items():
  if Path(name).name!=name or (HERE/name).is_symlink() or sha(HERE/name)!=h:raise ValueError('Hex source changed')
 for name,h in data['dependencies'].items():
  path=P/name
  if not path.resolve().is_relative_to(P.resolve()) or path.is_symlink() or sha(path)!=h:raise ValueError('Hex dependency changed')
 return data

def check_request(request,trial):
 if request.get('object')!='hexagonal_prism' or request.get('vertical_side_completion_authorized') is not True:raise ValueError('Explicit user prism completion request required')
 rid=request.get('run_id','')
 if not re.fullmatch('[0-9a-f]{32}',rid) or trial.get('run_id')!=rid or trial.get('session_id')!=request.get('session_id'):raise ValueError('Prism request belongs to another scan')
 if trial.get('baseline_PL_counter')!=request.get('baseline_PL_counter') or trial.get('release_manifest_sha256')!=request.get('capture_manifest_sha256'):raise ValueError('Prism acquisition binding differs')
 if trial.get('geometry_sha256')!=request.get('geometry_sha256'):raise ValueError('Prism geometry binding differs')
 return True

def run(request,output):
 sys.path.insert(0,str(P/'09_tools'));from board_pointcloud_export import board_identity
 if not board_identity()['is_linux_arm_zynq_or_pynq']:raise ValueError('Real prism computation requires Zynq')
 rid=request['run_id'];root=B/'runs'/rid;trial=json.loads((root/'trial.json').read_text());check_request(request,trial)
 latest=json.loads((B/'latest_result.json').read_text());status=json.loads((B/'status.json').read_text())
 if status.get('child_pid') is not None or latest.get('run_id')!=rid or latest.get('state')!='ready' or latest.get('discarded'):raise ValueError('Only current completed uncancelled scan can be completed')
 sys.path.insert(0,str(P/'86_integer_reference_fix_20261024'));from cleanup86 import inspect_cleanup
 inspect_cleanup(root,request['capture_manifest_sha256'])
 source=root/'model/object_points.csv';expected=sha(source);binding=json.loads((root/'model/viewer/export_manifest.json').read_text())
 if binding['outputs']['points.csv']['sha256']!=expected:raise ValueError('Measured CSV provenance differs')
 rows=list(csv.DictReader(source.open()));points=np.array([[float(r[k]) for k in ['x_mm','y_mm','z_mm']] for r in rows]);groups=np.array([int(r['point_index'])%36 for r in rows]);started=time.monotonic();model=estimate(points,groups)
 after=json.loads((B/'latest_result.json').read_text())
 if after.get('run_id')!=rid or after.get('state')!='ready' or after.get('discarded') or sha(source)!=expected:raise ValueError('Prism input no longer current')
 output=Path(output)
 if output.exists():raise ValueError('Prism proposal output must be new')
 output.mkdir(parents=True);sys.path.insert(0,str(P/'69_surface_visualization_20261024'));from surface_model import write_ply
 write_ply(output/'ESTIMATED_UPPER_SURFACE.ply',model['vertices'],model['triangles']);write_ply(output/'denoised_points.ply',model['cleaned']);write_ply(output/'measured_points.ply',points)
 data={'vertices':model['vertices'].tolist(),'faces':model['triangles'].tolist(),'points':model['cleaned'].tolist(),'weak':model['weak_faces'].astype(int).tolist(),'stats':model['stats'],'source_run':rid}
 (output/'model_viewer.html').write_text((HERE/'viewer_template.html').read_text().replace('__MODEL_DATA__',json.dumps(data)),encoding='utf-8')
 stats=model['stats'];stats['foreground_selection']={'foreground_points':len(points),'raw_candidates':len(points),'background_candidates_excluded':0,'height_range_mm':[min(float(r['height_above_reference_mm']) for r in rows),max(float(r['height_above_reference_mm']) for r in rows)],'empty_unique_angles_deg':[i*10 for i in range(36) if i not in set(groups)]}
 meta={'source_run_id':rid,'source_sha256':expected,'coordinate_producer':'zynq','derived_estimate':True,'shape_completion':True,'independent_engineering_proposal':True,'historical_algorithm_preview':False,'cancelled_during_modelling':False,'latest_result_modified':False,'processing_s':time.monotonic()-started,'operator_request':request,'statistics':stats,'face_kinds':model['face_kinds'],'source_scripts':{name:sha(HERE/name) for name in ['hex_model.py','hex_surface.py','viewer_template.html']}}
 save(output/'model_metadata.json',meta)
 save(output/'artifact_manifest.json',{'source_run_id':rid,'source_csv_sha256':expected,'coordinate_producer':'zynq','derived_estimate':True,'shape_completion':True,'current_when_generated':True,'files':{f.name:{'bytes':f.stat().st_size,'sha256':sha(f)} for f in output.iterdir() if f.is_file()}})
 return meta
