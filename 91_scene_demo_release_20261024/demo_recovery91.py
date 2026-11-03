"""Display-only recovery of complete86 scans rejected ONLY for endpoint support.

All XYZ remain exactly the captured Zynq coordinates. No hardware access.
The frozen acquisition, cleanup checks and original failed trial stay intact.
"""
from pathlib import Path
import csv,json,hashlib,math,os,sys,re
HERE=Path(__file__).resolve().parent;P=HERE.parent
PIN86=hashlib.sha256((HERE/'manifest.json').read_bytes()).hexdigest()
B=Path('/home/xilinx/jupyter_notebooks/button_scan')
ERROR='Measured endpoint closure failed'
def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
 p=Path(p);t=p.with_name(p.name+'.writing88');t.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');t.replace(p)
def policy(trial,closure,span,point_count):
 if trial.get('status')!='failed' or trial.get('error')!=ERROR:raise ValueError('Only endpoint-support failure is eligible')
 poses=trial.get('poses',[])
 if len(poses)!=37 or [p.get('nominal_deg') for p in poses]!=list(range(0,361,10)) or not all(p.get('status')=='completed' for p in poses):raise ValueError('Full completed37-pose acquisition required')
 if not math.isfinite(span) or abs(span-360)>.4:raise ValueError('Actual motor feedback did not close')
 for key in ['fixed_before','fixed_after']:
  if trial.get(key,{}).get('all_passed') is not True:raise ValueError('Fixed-scene reference failed')
 if trial.get('final_diagnostics',{}).get('completed_frames',-1)-trial.get('baseline_PL_counter',0)!=222:raise ValueError('Actual222 PL frames required')
 if point_count<10:raise ValueError('Insufficient actual object points even for a preview')
 n=closure.get('common_measured_object_columns',0);mm=closure.get('endpoint_distance_p95_mm');px=closure.get('endpoint_pixel_displacement_p95_px')
 if not (3<=n<20 and mm is not None and px is not None and math.isfinite(mm) and math.isfinite(px) and mm<=3 and px<=2):raise ValueError('Only common-column minimum20 relaxed to3; displacement limits unchanged')
 return {'eligible_for_demo_preview':True,'strict_closure_passed':False,'demonstration_only':True,'minimum_common_columns':3,'old_minimum_common_columns':20,'distance_p95_max_mm':3.,'pixel_p95_max_px':2.,'metric_accuracy_accepted':False,'quality_note':f'演示点云：首尾共同测点仅{n}列，低于原20列要求；保留全部真实测点，未通过完整闭合质量验收。'}
def check_current(root):
 root=Path(root);status=load(B/'status.json');latest=load(B/'latest_result.json')
 if status.get('run_id') not in [None,root.name] or latest.get('run_id')!=root.name or latest.get('discarded'):raise ValueError('Run is no longer current or was discarded')
 if status.get('state') in ['stop_requested','button_pressed','starting','recovery_required']:raise ValueError('A new start/stop or recovery is in progress')
 pid=status.get('child_pid')
 if pid:
  try:os.kill(pid,0)
  except ProcessLookupError:pass
  else:raise ValueError('Managed hardware child still exists')
 if (root/'scan/abort.request').exists():raise ValueError('Cancelled scan cannot be recovered')
 return status,latest
def prepare(root):
 root=Path(root).resolve()
 if root.parent!=(B/'runs').resolve() or not re.fullmatch('[0-9a-f]{32}',root.name):raise ValueError('Unexpected run path')
 sys.path[:0]=[str(HERE),str(P/'61_current_axis_laser_release_20261024'),str(P/'09_tools')]
 from cleanup91 import inspect_cleanup
 from fullturn_closure import compare_endpoints
 import board_pointcloud_export as export
 if not export.board_identity()['is_linux_arm_zynq_or_pynq']:raise ValueError('Real recovery must execute on Zynq')
 check_current(root);clean=inspect_cleanup(root,PIN86);trial=load(root/'trial.json')
 source=root/'model/object_points.csv';before=sha(source);trial_hash=sha(root/'trial.json')
 rows=list(csv.DictReader(source.open(newline='')))
 if not all(math.isfinite(float(r[k])) for r in rows for k in ['x_mm','y_mm','z_mm','y_px','x_px']):raise ValueError('Nonfinite measured data')
 ends=[{int(float(r['x_px'])):tuple(float(r[k]) for k in ['x_mm','y_mm','z_mm','y_px']) for r in rows if int(r['point_index'])==idx} for idx in [0,36]]
 closure=compare_endpoints(*ends);span=trial['poses'][-1]['motor_position_before_capture_deg']-trial['poses'][0]['motor_position_before_capture_deg']
 approval=policy(trial,closure,span,len(rows));closure['feedback_span_deg']=span
 note=approval['quality_note'];M=root/'model'
 receipt={'version':'demo_common3_preview_v1','run_id':root.name,'session_id':trial['session_id'],'capture_manifest_sha256':PIN86,'source_csv_sha256':before,'original_failed_trial_sha256':trial_hash,'original_error':trial['error'],'closure':closure,'demo_policy':approval,'cleanup':clean,'point_count':len(rows),'raw_coordinates_changed':False,'surface_generated':False,'coordinate_producer':'zynq','client_date':'2026-10-24'}
 meta={'title':'本轮真实点云 · 演示预览（首尾共同测点不足）','units':'mm','axis_units':dict.fromkeys('xyz','mm'),'data_origin':'hardware_scan','coordinate_producer':'zynq','synthetic':False,'diagnostic_preview_only':True,'alignment_validated':False,'metric_accuracy_accepted':False,'geometry_status':note,'coverage_note':'本轮为实际采集结果；缺测角度保留空缺，不添加预测测点。','geometry_sha256':trial['geometry_sha256'],'release_manifest_sha256':PIN86,'fullturn_closure':closure,'demonstration_only':True,'demo_quality_note':note,'source_run_id':root.name,'source_trial_sha256':trial_hash,'source_images':[{'path':str(root/'scan/camera_session/bright_after_session/bright_01.png'),'label':'本轮结束原图'}]}
 if not (M/'viewer/export_manifest.json').exists():
  save(M/'object_metadata.json',meta);export.export(source,M/'object_metadata.json',M/'viewer')
 else:
  manifest=load(M/'viewer/export_manifest.json')
  if manifest['outputs']['points.csv']['sha256']!=before or load(M/'viewer/source_meta.json').get('source_trial_sha256')!=trial_hash:raise ValueError('Existing preview provenance differs')
 check_current(root)
 if sha(source)!=before or sha(root/'trial.json')!=trial_hash:raise ValueError('Original evidence changed while exporting')
 save(root/'demo_recovery88.json',receipt)
 return receipt
def record_for(root,receipt):
 root=Path(root);sys.path.insert(0,str(P/'73_pointcloud_review_first_20261024'))
 from pointcloud_review import review
 if not (root/'model/pointcloud_review/review_manifest.json').exists():review(root)
 assessment=load(root/'model/pointcloud_review/review.json')
 return {'schema_version':1,'session_id':receipt['session_id'],'run_id':root.name,'state':'ready','result_kind':'diagnostic_preview','diagnostic_preview_only':True,'alignment_validated':False,'metric_accuracy_accepted':False,'viewer_relative':'runs/'+root.name+'/model/viewer','export_manifest_sha256':sha(root/'model/viewer/export_manifest.json'),'point_count':receipt['point_count'],'review_relative':'runs/'+root.name+'/model/pointcloud_review','review_manifest_sha256':sha(root/'model/pointcloud_review/review_manifest.json'),'surface_review_required':True,'demonstration_only':True,'demo_quality_note':receipt['demo_policy']['quality_note'],'endpoint_closure':receipt['closure'],'empty_unique_angles_deg':assessment['empty_unique_angles_deg'],'recovery_receipt_sha256':sha(root/'demo_recovery88.json')}
def publish_current(root):
 root=Path(root);receipt=prepare(root);record=record_for(root,receipt);status,latest=check_current(root)
 save(root/'latest_before_recovery88.json',latest)
 check_current(root);save(root/'result.json',record);save(B/'latest_result.json',record)
 status.update(state='ready_diagnostic_preview',child_pid=None,error=None,completed_poses=37,total_poses=37,current_angle_deg=360,elapsed_seconds=load(root/'trial.json')['elapsed_s'],demonstration_only=True,demo_quality_note=record['demo_quality_note'])
 save(B/'status.json',status)
 return record
