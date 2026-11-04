"""Derive a new scan release from frozen98 with current geometry/reference binding only."""
from pathlib import Path
import json,hashlib,shutil,ast
P=Path('/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020');S=P/'98_optical_identity_fix_20261024';D=P/'101_relocation_scan_20261102';G=P/'100_relocation_geometry_20261102'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
base=load(S/'manifest.json');assert sha(S/'manifest.json')=='e8f94e21ae33c6e8c92099c1df6ab41c5b4c12d04d81cf27a7d1a0f98307c82d'
assert all(sha(S/n)==h for n,h in base['files'].items());assert G.is_dir();D.mkdir(exist_ok=False)
for n in base['files']:shutil.copyfile(S/n,D/n)
version='20261102.relocation_geometry.v1'
for n in ['optimized_dual_scan.py','cleanup91.py']:
 f=D/n;s=f.read_text().replace('20261024.autoWB_red_identity.v1',version)
 if n=='optimized_dual_scan.py':
  s=s.replace('94_current_geometry_20261024','100_relocation_geometry_20261102').replace('config_current_axis_20261024.json','config_relocation_20261102.json').replace('93_recalibration_20261024/zero_capture_02/','99_relocation_calibration_20261102/zero_capture_01/').replace('[(134,177,170,202),(408,176,443,201),(65,366,111,405),(462,368,510,408)]','[(140,184,172,212),(447,195,477,222),(85,319,123,351),(476,346,513,376)]')
 f.write_text(s)
f=D/'service91.py';s=f.read_text().replace('94_current_geometry_20261024','100_relocation_geometry_20261102').replace('f0843504bde34b4000ea5fa8de054b3b4bce78334f017f67e08169e6436bd895',sha(G/'current_geometry.json'));f.write_text(s)
f=D/'compat_cleanup.py';s=f.read_text().replace("PIN96='", "PIN98='e8f94e21ae33c6e8c92099c1df6ab41c5b4c12d04d81cf27a7d1a0f98307c82d'\nPIN96='")
s=s.replace("old96=historical('96_resilient_preview_20261024','release91','cleanup91')","old96=historical('96_resilient_preview_20261024','release91','cleanup91')\nold98=historical('98_optical_identity_fix_20261024','release91','cleanup91')").replace('[PIN86,PIN91,PIN95,PIN96,new]','[PIN86,PIN91,PIN95,PIN96,PIN98,new]').replace('if actual==PIN96:return old96.inspect_cleanup(path,PIN96)','if actual==PIN96:return old96.inspect_cleanup(path,PIN96)\n if actual==PIN98:return old98.inspect_cleanup(path,PIN98)');f.write_text(s)
manifest={k:v for k,v in base.items() if k!='files'};manifest['version']=version;manifest['profile'].update(calibration_release=G.name,geometry_sha256=sha(G/'current_geometry.json'),client_date='2026-11-02')
manifest['inherited_files'].update({('98_optical_identity_fix_20261024/'+n):h for n,h in base['files'].items()});manifest['inherited_files']['98_optical_identity_fix_20261024/manifest.json']=sha(S/'manifest.json')
manifest['inherited_files'].update({p.relative_to(P).as_posix():sha(p) for p in G.iterdir() if p.is_file()})
ref=P/'99_relocation_calibration_20261102/zero_capture_01/camera_session/bright_before_session/bright_01.png';manifest['inherited_files'][ref.relative_to(P).as_posix()]=sha(ref)
manifest['files']={n:sha(D/n) for n in base['files']}
for f in D.glob('*.py'):ast.parse(f.read_text())
(D/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
B=Path('/home/xilinx/jupyter_notebooks/button_scan');cfg=load(B/'config_current_axis_20261024.json');cfg['client_date']='2026-11-02';(B/'config_relocation_20261102.json').write_text(json.dumps(cfg,indent=2)+'\n')
print(json.dumps({'capture_release':D.name,'manifest_sha256':sha(D/'manifest.json'),'geometry_sha256':sha(G/'current_geometry.json'),'version':version}))
