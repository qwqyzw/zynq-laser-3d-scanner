from pathlib import Path
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
