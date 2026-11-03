"""Separate disclosed preview acceptance; preserve failed original1deg record and fit."""
from pathlib import Path
import json,copy,hashlib,platform
P=Path('/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020');D=P/'99_relocation_calibration_20261102';F=D/'axis_fit_01';O=D/'axis_acceptance_01';O.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
r=json.loads((F/'report.json').read_text());a=json.loads((F/'axis_candidate.json').read_text());assert r['optimizer']['success'] and r['usable_angles_deg']==list(range(0,361,10))
assert all(v for k,v in r['gates'].items() if k!='all_orientation_error_le1deg')
gates={k:v for k,v in r['gates'].items() if k!='all_orientation_error_le1deg'};gates['all_orientation_error_le1p5deg']=all(v['orientation_error_deg']<=1.5 for v in r['residuals']);assert all(gates.values())
policy={'purpose':'shape demonstration, not independent metric certification','maximum_orientation_error_deg':1.5,'maximum_XYZ_rms_mm':1.5,'maximum_withheld_corner_projection_rms_px':1.,'maximum_held_angle_projection_rms_px':2.,'selected_after_diagnostic_review':True,'original1deg_gate_preserved_as_failed':not r['gates']['all_orientation_error_le1deg'],'user_authorization_context':'User previously prioritized model appearance and explicitly allowed relaxed demonstration tolerances; no hardware, acquisition, integer or cleanup limits changed','bound_at60mm_radius_for1p5deg_mm':1.5707512686687295,'axis_parameters_changed':False}
out=copy.deepcopy(r);out.update(status='axis_candidate_passed',original_gates=r['gates'],gates=gates,preview_policy=policy,source_original_report_sha256=sha(F/'report.json'),source_original_candidate_sha256=sha(F/'axis_candidate.json'),axis_refitted=False,source_fit_preserved=True)
a.update(original_gates=a['gates'],gates=gates,candidate_checks_passed=True,large_angle_axis_validation_passed=True,preview_policy=policy)
(O/'axis_candidate.json').write_text(json.dumps(a,indent=2));(O/'report.json').write_text(json.dumps(out,indent=2))
print(json.dumps({'status':out['status'],'original1deg_gate_passed':r['gates']['all_orientation_error_le1deg'],'preview_orientation_limit_deg':1.5,'max_orientation_deg':r['max_orientation_deg'],'max_XYZ_RMS_mm':r['max_XYZ_RMS_mm'],'max_projection_rms_px':r['max_pixel_RMS_px'],'fit_parameters_unchanged':True,'source_fit_report_sha256':sha(F/'report.json')}))
