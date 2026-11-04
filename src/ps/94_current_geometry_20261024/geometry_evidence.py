"""Only the newly bound calibration; imported without hardware access."""
from pathlib import Path
import json,hashlib
G=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
def validate_geometry_evidence(model):
 manifest=load(G/'manifest.json')
 for name,h in manifest['files'].items():
  if Path(name).name!=name or (G/name).is_symlink() or sha(G/name)!=h:raise ValueError('Calibration identity changed: '+name)
 if model!=load(G/'current_geometry.json'):raise ValueError('Geometry differs from bound current calibration')
 a=load(G/'axis_candidate.json');ar=load(G/'axis_acceptance_report.json');t=load(G/'laser_training_report.json');h=load(G/'laser_holdout_report.json');l=load(G/'laser_candidate.json')
 full=load(G/'full_target_axis_check.json')
 if ar['full_target_validation_sha256']!=sha(G/'full_target_axis_check.json') or a['full_target_validation_sha256']!=sha(G/'full_target_axis_check.json') or full['axis_refitted'] is not False or full['max_orientation_deg']>1 or full['max_XYZ_RMS_mm']>1.5:raise ValueError('Full target pose validation differs')
 if not a['candidate_checks_passed'] or not all(a['gates'].values()) or ar['max_target_XYZ_RMS_mm']>1.5 or ar['max_orientation_deg']>1:raise ValueError('Current axis preview checks failed')
 if ar['axis_candidate_sha256']!=sha(G/'axis_candidate.json') or t['axis_candidate_sha256']!=sha(G/'axis_candidate.json'):raise ValueError('Axis evidence mixed')
 if t['fit_heights_mm']!=[0,12] or t['holdout_used_for_fit'] is not False or t['fit']['rms_mm']>.5:raise ValueError('Two-height training rejected')
 if h['status']!='current_axis_laser_holdout_passed' or not all(h['gates'].values()) or h['holdout_used_for_fit'] is not False or h['training_report_sha256']!=sha(G/'laser_training_report.json') or h['height_p95_absolute_error_mm']>3 or h['plane_p95_residual_mm']>2:raise ValueError('Independent height check failed')
 for k in ['normal','offset_mm']:
  if model['laser_plane'][k]!=l[k] or model['reference_plane'][k]!=a['reference_plane'][k]:raise ValueError('Laser/reference plane identity differs')
 if model['metric_accuracy_accepted'] is not False or model['historical_extrinsics_used'] is not False:raise ValueError('Invalid acceptance scope')
 return {'diagnostic_preview_only':True,'alignment_validated':False,'rough_alignment_checks_passed':True,'current_geometry_revalidated':True,'metric_accuracy_accepted':False,'historical_extrinsics_reused':False,'geometry_reuse_authorized':False,'laser_plane_preview_validated':True,'axis_revalidation_skipped_by_user':False,'full_axis_validation_passed':True,'original_strict_axis_checks_passed':ar['strict0p75mm_checks_passed'],'calibration_scope':'37 captured/35 usable checker poses,130/140 false detections excluded;0+12mm laser fit, independent33.5mm holdout; rough shape preview','laser_holdout_height_p95_error_mm':h['height_p95_absolute_error_mm']}
