"""Explicit programmatic test using the same managed coordinator as BTN0.

No fabricated GPIO press. Original finite movement and abort behavior remain.
Only executed by an authorized operator, never by normal systemd startup.
"""
from pathlib import Path
import sys,os,json,time,subprocess,signal
HERE=Path(__file__).resolve().parent;P=HERE.parent;B=Path('/home/xilinx/jupyter_notebooks/button_scan')
sys.path.insert(0,str(HERE))
import dual_button_service as m

class Once(m.Coordinator):
 def __init__(self,*args,**kwargs):self.test_started=False;super().__init__(*args,**kwargs)
 def poll(self):
  super().poll()
  if self.test_started and self.process is None:self.exiting=True;return
  if not self.test_started and self.gate.armed and int(self.gpio.read(0))&3==0:
   self.test_started=True;self.audit({'kind':'authorized_programmatic_trial_start','physical_BTN0':False,'monotonic_s':time.monotonic()});self.start()

def main():
 assert __import__('platform').machine() in ['armv7l','aarch64']
 before=m.original.load(B/'status.json');assert before['child_pid'] is None and before['state'] in ['ready','ready_diagnostic_preview','waiting_for_release']
 assert subprocess.run(['fuser','/dev/video0'],capture_output=True).returncode==1
 unit=subprocess.check_output(['systemctl','cat','zynq-button-scan.service'],text=True);assert str(HERE/'dual_button_service.py') in unit
 pin=(HERE/'manifest_pin.txt').read_text().strip();binding=m.verify_release(pin);old,identity,base=m.base.verify(binding['base73_manifest_sha256']);cfg,art=m.base.base.base.base.legacy.inspect_config(B/'config_current_axis_20261024.json');cfg['config_file']=(B/'config_current_axis_20261024.json').resolve()
 subprocess.run(['systemctl','stop','zynq-button-scan.service'],check=True,timeout=20)
 # Idle maintenance must not rewrite the current/latest result.
 c=Once(cfg,art,dual_manifest_sha256=pin,review_manifest_sha256=binding['base73_manifest_sha256'],visual_manifest_sha256=old['base71_manifest_sha256'],compat_manifest_sha256=base['base68_manifest_sha256'],startup_manifest_sha256=identity['base66_manifest_sha256'])
 os.environ['XILINX_XRT']='/usr';os.environ['BOARD']='Pynq-Z2'
 c.serve()
 final=m.original.load(B/'status.json');assert final['child_pid'] is None
 if c.run is not None:
  from cleanup85 import inspect_cleanup
  inspect_cleanup(c.run,pin)
 assert subprocess.run(['fuser','/dev/video0'],capture_output=True).returncode==1
 summary={'run_id':c.run_id,'session_id':c.session_id,'physical_BTN0':False,'supervised_trial_complete':True,'latest_result':m.original.load(B/'latest_result.json')}
 m.original.save(HERE/'supervised_trial_result.json',summary)
 subprocess.run(['systemctl','start','zynq-button-scan.service'],check=True,timeout=20)
 print(json.dumps(summary))
if __name__=='__main__':main()
