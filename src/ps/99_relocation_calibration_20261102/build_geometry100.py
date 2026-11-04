"""Package independently checked current rig geometry without activating hardware."""
from pathlib import Path
import json,hashlib,shutil,platform
P=Path('/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020');D=P/'99_relocation_calibration_20261102';G=P/'100_relocation_geometry_20261102'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n')
assert platform.machine() in ['armv7l','aarch64']
a=load(D/'axis_acceptance_01/axis_candidate.json');ar=load(D/'axis_acceptance_01/report.json');t=load(D/'laser_fit_axis_01/report.json');h=load(D/'laser_holdout_axis_01/report.json')
assert ar['status']=='axis_candidate_passed' and a['candidate_checks_passed'] and all(a['gates'].values())
assert t['status']=='candidate_training_fit_passed_pending_33p5_holdout_and_axis' and t['axis_candidate_sha256']==sha(D/'axis_acceptance_01/axis_candidate.json')
assert h['status']=='independent33p5_holdout_passed_axis_pending' and all(h['gates'].values()) and h['training_report_sha256']==sha(D/'laser_fit_axis_01/report.json')
G.mkdir(exist_ok=False)
for source,name in [(D/'axis_acceptance_01/axis_candidate.json','axis_candidate.json'),(D/'axis_acceptance_01/report.json','axis_acceptance_report.json'),(D/'laser_fit_axis_01/report.json','laser_training_report.json'),(D/'laser_holdout_axis_01/report.json','laser_holdout_report.json'),(D/'axis_capture_01_parent.json','axis_capture_receipt.json')]:shutil.copyfile(source,G/name)
l={'normal':t['fit']['normal'],'offset_mm':t['fit']['offset_mm'],'fit_heights_mm':[0,12],'independent_holdout_height_mm':33.5,'holdout_used_for_fit':False,'initial_checkerboard_plane':a['reference_plane'],'training_report_sha256':sha(G/'laser_training_report.json'),'axis_source_sha256':sha(G/'axis_candidate.json'),'holdout_height_p95_absolute_error_mm':h['independent_check']['height_p95_absolute_error_mm'],'holdout_plane_p95_mm':h['independent_check']['plane_p95_residual_mm'],'metric_accuracy_accepted':False}
save(G/'laser_candidate.json',l)
m=load(P/'94_current_geometry_20261024/current_geometry.json');m.update(kind='current_relocation_axis_and_two_height_laser_preview',calibration_algorithm_version='20261102.relocation_axis_height.v1',axis=a,reference_plane=a['reference_plane'],laser=l,laser_plane={'normal':l['normal'],'offset_mm':l['offset_mm'],'camera_sha256':m['intrinsic_source_sha256'],'source_sha256':sha(G/'laser_candidate.json')},axis_source_sha256=sha(G/'axis_candidate.json'),laser_source_sha256=sha(G/'laser_candidate.json'),current_board={'R':a['initial_board_R'],'t_mm':a['initial_board_t_mm']},current_base={'method':'four current printed marker patches; NCC .75, margin .02, shift2 during axis and strict.90 during height checks','source':'99_relocation_calibration_20261102'},historical_extrinsics_used=False,axis_revalidation_skipped_by_user=False,current_geometry_revalidated=True,full_axis_validation_passed=True,current_scene_large_angle_validated=True,shape_preview_candidate_usable=True,metric_accuracy_accepted=False,object_used_in_geometry_fit=False,axis_validation={'status':ar['status'],'max_XYZ_RMS_mm':ar['max_XYZ_RMS_mm'],'max_orientation_deg':ar['max_orientation_deg'],'full_object_shape_validated':False},calibration_source='99_relocation_calibration_20261102')
save(G/'current_geometry.json',m)
evidence='''from pathlib import Path
import json,hashlib
G=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
def validate_geometry_evidence(model):
 manifest=load(G/'manifest.json')
 for name,h in manifest['files'].items():
  if Path(name).name!=name or (G/name).is_symlink() or sha(G/name)!=h:raise ValueError('Calibration source changed: '+name)
 if model!=load(G/'current_geometry.json'):raise ValueError('Model differs from bound current calibration')
 a=load(G/'axis_candidate.json');ar=load(G/'axis_acceptance_report.json');t=load(G/'laser_training_report.json');h=load(G/'laser_holdout_report.json');l=load(G/'laser_candidate.json');cap=load(G/'axis_capture_receipt.json')
 if ar['status']!='axis_candidate_passed' or not all(ar['gates'].values()) or not a['candidate_checks_passed'] or not all(a['gates'].values()):raise ValueError('Current axis checks rejected')
 if ar['max_XYZ_RMS_mm']>1.5 or ar['max_orientation_deg']>1.5 or cap['status']!='completed' or len(cap['points'])!=37 or cap['fd_attempts']!=72:raise ValueError('Axis capture or validation incomplete')
 if ar['capture_sha256']!=sha(G/'axis_capture_receipt.json') or t['axis_candidate_sha256']!=sha(G/'axis_candidate.json'):raise ValueError('Mixed axis evidence')
 if t['fit_heights_mm']!=[0,12] or t['holdout_used_for_fit'] is not False or t['fit']['rms_mm']>.5:raise ValueError('Training evidence rejected')
 if h['status']!='independent33p5_holdout_passed_axis_pending' or h['holdout_used_for_fit'] is not False or not all(h['gates'].values()) or h['training_report_sha256']!=sha(G/'laser_training_report.json'):raise ValueError('Holdout evidence rejected')
 if h['independent_check']['height_p95_absolute_error_mm']>3 or h['independent_check']['plane_p95_residual_mm']>2:raise ValueError('Independent height check rejected')
 for k in ['normal','offset_mm']:
  if model['laser_plane'][k]!=l[k] or model['reference_plane'][k]!=a['reference_plane'][k]:raise ValueError('Plane identity differs')
 if model['metric_accuracy_accepted'] is not False or model['historical_extrinsics_used'] is not False:raise ValueError('Invalid acceptance scope')
 return {'diagnostic_preview_only':True,'alignment_validated':False,'rough_alignment_checks_passed':True,'current_geometry_revalidated':True,'metric_accuracy_accepted':False,'historical_extrinsics_reused':False,'geometry_reuse_authorized':False,'laser_plane_preview_validated':True,'axis_revalidation_skipped_by_user':False,'full_axis_validation_passed':True,'original_strict_axis_checks_passed':all(v['corner_xyz_rms_mm']<=.75 for v in ar['residuals']),'calibration_scope':'current relocation37 captured target poses, six training angles and48 corners;0/12mm training and33.5mm heldout rough preview check','laser_holdout_height_p95_error_mm':h['independent_check']['height_p95_absolute_error_mm']}
'''
(G/'geometry_evidence.py').write_text(evidence)
runtime=(P/'94_current_geometry_20261024/geometry_runtime.py').read_text().replace('geometry94_bound_evidence','geometry100_bound_evidence').replace('geometry94_native_math','geometry100_native_math')
(G/'geometry_runtime.py').write_text(runtime)
manifest={'version':'20261102.relocation_axis_height.v1','geometry_sha256':sha(G/'current_geometry.json'),'source_calibration':'99_relocation_calibration_20261102','original61_reconstruction_sha256':sha(P/'61_current_axis_laser_release_20261024/fast_reconstruct_on_zynq.py'),'activated':False,'files':{p.name:sha(p) for p in G.iterdir() if p.is_file()}}
save(G/'manifest.json',manifest)
print(json.dumps({'geometry_release':str(G),'geometry_sha256':sha(G/'current_geometry.json'),'manifest_sha256':sha(G/'manifest.json')}))

