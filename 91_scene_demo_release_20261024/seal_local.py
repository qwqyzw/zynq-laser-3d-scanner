from pathlib import Path
import json,hashlib,ast
D=Path(__file__).resolve().parent;P=D.parent
policy={'maximum_shift_px':4.0,'minimum_correlation':.75,'minimum_peak_margin':.02,'all_four_references_required':True,'old_maximum_shift_px':2.0,'old_minimum_correlation':.9,'demonstration_only':True,'metric_accuracy_accepted':False}
(D/'scene_policy.json').write_text(json.dumps(policy,indent=2),encoding='utf-8')
test=(P/'86_integer_reference_fix_20261024/test_cleanup86.py').read_text(encoding='utf-8').replace('import cleanup86 as cleanup','import cleanup91 as cleanup').replace('20261024.integer_tie_guard.v1','20261024.scene_demo.v1').replace("'foreground_select.py']","'foreground_select.py','scene_limits.py']")
(D/'test_cleanup91.py').write_text(test,encoding='utf-8')
base=json.loads((D/'inherited_manifest_template.json').read_text())
base['version']='20261024.scene_demo.v1';base['profile']['scene_demo_limits']=policy
files=['button_audit.py','diagnostic_camera.py','dual_button_service.py','foreground_select.py','fuse_measured_profiles.py','locked_switchable_camera_worker.py','optimized_dual_scan.py','roi_integer.py','switchable_pl_camera.py','temporal_consensus.py','release91.py','cleanup91.py','scene_limits.py','scene_policy.json','compat_cleanup.py','service91.py','demo_recovery91.py','model_compat.py']
base['files']={n:hashlib.sha256((D/n).read_bytes()).hexdigest() for n in files}
base['inherited_files']['90_export_lifecycle_fix_20261024/failure_reason.py']=hashlib.sha256((P/'90_export_lifecycle_fix_20261024/failure_reason.py').read_bytes()).hexdigest()
for n in files:
 if n.endswith('.py'):ast.parse((D/n).read_text(encoding='utf-8'),filename=n)
(D/'manifest.json').write_text(json.dumps(base,ensure_ascii=False,indent=2),encoding='utf-8')
print('local manifest '+hashlib.sha256((D/'manifest.json').read_bytes()).hexdigest())
