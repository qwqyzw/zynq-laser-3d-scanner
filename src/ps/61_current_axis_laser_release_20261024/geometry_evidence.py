"""File-only identity and CURRENT axis/laser preview evidence checks."""
from pathlib import Path
import json,hashlib
R=Path(__file__).resolve().parent
PINS={'axis_candidate.json': 'c5ef4e93a527770d6e69e7af9b7f2328612e187d3447da340245315ccf34f071', 'axis_acceptance_report.json': '3b1a07c7e730e2f3425efb4f54995592b0e9a1f332badc4b02b6a0068630a504', 'laser_candidate.json': '7b75e5e5651f1ff0aa0c85d3349ebd1f24ab5ec3139e4341d9b752d8ecabeb49', 'laser_training_report.json': 'f8c27f8dc5e09b49c41a5c1ffcb7c76443a459634a41467372355ceeb5d2681c', 'laser_holdout_report.json': '9b5583e28c920f011581bfaaaf3ea9f6e72746b9069c28ff45f04cd4f2ac276f'}
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def validate_geometry_evidence(model):
 for name,expected in PINS.items():
  if digest(R/name)!=expected:raise ValueError('Calibration evidence changed: '+name)
 a=load(R/'axis_candidate.json');ar=load(R/'axis_acceptance_report.json');l=load(R/'laser_candidate.json');t=load(R/'laser_training_report.json');h=load(R/'laser_holdout_report.json')
 if not a['candidate_checks_passed'] or not a['large_angle_axis_validation_passed'] or not all(a['gates'].values()) or a.get('original_strict_checks_passed') is not False:raise ValueError('Axis rough/strict evidence differs')
 if ar['status']!='current_axis_rough_preview_checks_passed' or ar['axis_candidate_sha256']!=PINS['axis_candidate.json'] or ar['max_target_XYZ_RMS_mm']>1.5 or ar['max_orientation_deg']>1:raise ValueError('Current target axis validation failed')
 if l['fit_heights_mm']!=[0,12] or l['independent_holdout_height_mm']!=33.5 or l['holdout_used_for_fit'] is not False or l['axis_source_sha256']!=PINS['axis_candidate.json']:raise ValueError('Laser fit/holdout/axis identity mixed')
 if h['status']!='current_axis_laser_holdout_passed' or not all(h['gates'].values()) or h['training_report_sha256']!=PINS['laser_training_report.json'] or h['holdout_plane_p95_mm']>2 or h['holdout_height_p95_absolute_error_mm']>3:raise ValueError('Fresh laser independent check failed')
 if t['status']!='two_height_training_fit_passed_pending_holdout' or t['fit']['rms_mm']>.5:raise ValueError('Fresh training laser rejected')
 for name in ['axis_direction_camera','axis_point_on_initial_paper_mm','turntable_basis_camera_columns','motor_zero_reference_deg','initial_board_R','initial_board_t_mm']:
  if model['axis'][name]!=a[name]:raise ValueError('Current axis changed: '+name)
 for key in ['normal','offset_mm']:
  if model['laser_plane'][key]!=l[key] or model['reference_plane'][key]!=l['initial_checkerboard_plane'][key]:raise ValueError('Current laser/reference changed')
 if model.get('historical_extrinsics_used') is not False or model.get('axis_revalidation_skipped_by_user') is not False or model.get('metric_accuracy_accepted') is not False or model.get('current_geometry_revalidated') is not True:raise ValueError('Current geometry acceptance flags changed')
 return {'diagnostic_preview_only':True,'alignment_validated':False,'rough_alignment_checks_passed':True,'current_geometry_revalidated':True,'metric_accuracy_accepted':False,'historical_extrinsics_reused':False,'geometry_reuse_authorized':False,'laser_plane_preview_validated':True,'axis_revalidation_skipped_by_user':False,'full_axis_validation_passed':True,'calibration_scope':'current glued checker axis0..360 rough preview; new0+12mm laser with independent33.5mm holdout; full object shape and metric accuracy unaccepted','original_strict_axis_checks_passed':False,'laser_holdout_height_p95_error_mm':h['holdout_height_p95_absolute_error_mm']}
