from pathlib import Path
import json,time,sys,argparse,shutil
from hex_surface import HERE,P,B,sha,save,verify,run

def daemon(pin,request_pin):
 verify(pin);path=HERE/'operator_request.json'
 if sha(path)!=request_pin:raise ValueError('Operator request changed')
 request=json.loads(path.read_text());rid=request['run_id'];target=HERE/'algorithm_previews'/rid;attempted=False
 while True:
  latest=json.loads((B/'latest_result.json').read_text());status=json.loads((B/'status.json').read_text())
  if latest.get('run_id')==rid and latest.get('discarded'):
   if target.exists():
    if target.is_symlink() or target.resolve().parent!=(HERE/'algorithm_previews').resolve():raise ValueError('Invalid prism discard path')
    manifest=json.loads((target/'artifact_manifest.json').read_text())
    if manifest['source_run_id']!=rid:raise ValueError('Discard identity differs')
    for name,row in manifest['files'].items():
     if Path(name).name!=name or (target/name).is_symlink() or sha(target/name)!=row['sha256']:raise ValueError('Prism discard manifest differs')
    shutil.rmtree(target)
   save(HERE/'job_status.json',{'run_id':rid,'state':'cancelled','derived_removed':True,'hardware_accessed':False});return
  if latest.get('run_id')==rid and latest.get('state')=='ready' and status.get('child_pid') is None and not attempted:
   attempted=True
   try:
    verify(pin)
    if sha(path)!=request_pin:raise ValueError('Operator request changed')
    save(HERE/'job_status.json',{'run_id':rid,'state':'building_hex_prism','hardware_accessed':False})
    if target.exists():
     m=json.loads((target/'artifact_manifest.json').read_text())
     if m['source_run_id']!=rid or m['source_csv_sha256']!=sha(B/'runs'/rid/'model/object_points.csv'):raise ValueError('Existing prism source differs')
     for name,row in m['files'].items():
      if Path(name).name!=name or sha(target/name)!=row['sha256']:raise ValueError('Existing prism output differs')
    else:run(request,target)
    save(HERE/'job_status.json',{'run_id':rid,'state':'hex_prism_display_ready','hardware_accessed':False,'all_side_faces_estimated':True})
   except Exception as e:save(HERE/'job_status.json',{'run_id':rid,'state':'failed_raw_cloud_retained','error':str(e),'hardware_accessed':False})
  if latest.get('run_id')==rid and latest.get('state')=='failed':
   save(HERE/'job_status.json',{'run_id':rid,'state':'acquisition_failed_no_prism','hardware_accessed':False});return
  time.sleep(2)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--manifest-sha256',required=True);p.add_argument('--request-sha256',required=True);a=p.parse_args();daemon(a.manifest_sha256,a.request_sha256)
