from pathlib import Path
import json,subprocess
B=Path('/home/xilinx/jupyter_notebooks/button_scan');R=B/'runs/0de78f1c77684877972e84d445880b69'
def load(p):return json.loads(p.read_text())
t=load(R/'trial.json') if (R/'trial.json').exists() else {}
print(json.dumps({'status':load(B/'status.json'),'latest':load(B/'latest_result.json'),'trial':{k:v for k,v in t.items() if k not in ['poses','motion_log','stationary_before','foreground_selection']},'pose_count':len(t.get('poses',[])),'model_files':[str(p.relative_to(R)) for p in (R/'model').rglob('*')] if (R/'model').exists() else [],'root_files':[p.name for p in R.iterdir()] if R.exists() else []},ensure_ascii=False))
for p in [R/'acquiring.log',B/'button_events.jsonl']:
 if p.exists():print(p.name+'\n'+'\n'.join(p.read_text(errors='replace').splitlines()[-14:]))
print(subprocess.check_output(['journalctl','-u','zynq-button-scan.service','-n','35','--no-pager'],text=True))
