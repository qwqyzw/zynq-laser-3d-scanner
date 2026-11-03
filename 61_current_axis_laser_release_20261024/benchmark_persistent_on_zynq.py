"""Stationary original/continuous camera comparison, no motor control commands."""
from pathlib import Path
import argparse,fcntl,json,subprocess,sys,time,traceback
import numpy as np
R=Path(__file__).resolve().parent;P=R.parent
sys.path.insert(0,str(R));sys.path.insert(0,str(P/'09_tools'))
sys.path.insert(0,str(P/'04_runtime'));sys.path.insert(0,str(P/'01_fpga_baseline/reference'))
import object_board_scan_360 as scan
from fast_release import verify_release,load,digest
from persistent_pl_camera import PersistentPLCamera

def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest-sha256',required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 verify_release(P,R,args.manifest_sha256);root=args.output.resolve();root.mkdir(exist_ok=False)
 report={'status':'starting','client_date':'2026-10-21','motor_control_commands_sent':False,'FPGA_downloaded':False,
         'coordinate_computation_performed':False,'manifest_sha256':args.manifest_sha256,'batches':[],
         'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
 locks=[];runtime=motor=camera=None;counter=None
 def save():
  p=root/'benchmark.json';t=p.with_name(p.name+'.writing');t.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False));t.replace(p)
 def stationary():
  rows=[]
  for _ in range(3):
   status=motor.query('status');position=motor.query('position',firmware_family=family)
   if not status['position_reached'] or status['stall_detected'] or status['stall_protection_triggered']:raise RuntimeError('Motor not stationary')
   rows.append(position['motor_position_deg']);time.sleep(.15)
  if max(rows)-min(rows)>.1:raise RuntimeError('Moving feedback')
  return rows
 try:
  if subprocess.run(['systemctl','is-active','zynq-button-scan.service'],capture_output=True,text=True).stdout.strip()!='inactive':raise RuntimeError('Button service must be inactive')
  for name in ['/tmp/laser_motor_button_service.lock','/tmp/laser_motor_object_scan.lock']:
   f=open(name,'a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
  users=subprocess.run(['fuser','/dev/video0'],capture_output=True,text=True)
  if users.returncode!=1 or users.stdout.strip():raise RuntimeError('Camera occupied')
  _,artifacts,_=scan.inspect(P,360,(175,270,410,320),5,button_release=P/'17_button_scan_release_20261020')
  mods=scan.load_modules(P,board=True);runtime=scan.attach_loaded_runtime(P,mods,artifacts)
  counter=runtime.initial_diagnostics['completed_frames'];report['baseline_frames']=counter;report['overlay_before']=runtime.identity
  motor=mods['motor_uartlite'].UARTLiteReadOnlyClient(runtime.overlay);family=motor.query('configuration')['firmware_family_layout']
  report['motor_before']=stationary();folder=root/'control_original';folder.mkdir()
  camera=mods['pl_scan_camera'].PLStripeCheckedCamera((P/'04_runtime/fixed_exposure_worker.py').read_text(),(175,270,410,320),runtime.driver,verify_ps=True,exposure=5,timeout_s=40)
  started=time.monotonic();camera.start(folder);batch=camera.capture(folder/'images');elapsed=time.monotonic()-started
  counter=scan.verify_capture(folder,folder/'images',batch,runtime.driver,counter)
  report['original_cleanup']=scan.shutdown_camera(camera);camera=None
  report['control_batch_s']=elapsed;report['batches'].append({'label':'control_original','elapsed_s':elapsed,'batch':batch});save()
  folder=root/'persistent';folder.mkdir();camera=PersistentPLCamera((R/'persistent_camera_worker.py').read_text(),(175,270,410,320),runtime.driver,verify_ps=True,exposure=5,timeout_s=40)
  started=time.monotonic();report['session_start']=camera.start(folder);report['session_start_s']=time.monotonic()-started
  for i in range(6):
   dest=folder/('batch_%02d'%i)/'images';started=time.monotonic();batch=camera.capture(dest);elapsed=time.monotonic()-started
   counter=scan.verify_capture(folder,dest,batch,runtime.driver,counter)
   report['batches'].append({'label':'persistent_%02d'%i,'elapsed_s':elapsed,'batch':batch});save()
   print('PERSIST_BATCH',i,elapsed,batch['stripe_quality']['common_quality_columns'],flush=True)
  started=time.monotonic();report['persistent_cleanup']=scan.shutdown_camera(camera);camera=None;report['session_close_s']=time.monotonic()-started
  if not report['persistent_cleanup'].get('camera_release_confirmed') or report['persistent_cleanup'].get('camera_close_error'):raise RuntimeError('Persistent close failed')
  reference=np.loadtxt(root/'control_original/images/stripe_pixels.csv',delimiter=',',skiprows=1).reshape(-1,2);reference={int(x):float(y) for x,y in reference}
  comparisons=[]
  for i in range(6):
   values=np.loadtxt(folder/('batch_%02d'%i)/'images/stripe_pixels.csv',delimiter=',',skiprows=1).reshape(-1,2);values={int(x):float(y) for x,y in values}
   common=sorted(reference.keys()&values.keys());d=np.array([abs(reference[x]-values[x]) for x in common]);passed=bool(len(common)>=20 and np.percentile(d,95)<=.5 and d.max()<=1)
   comparisons.append({'batch':i,'common_columns':len(common),'median_px':float(np.median(d)),'p95_px':float(np.percentile(d,95)),'maximum_px':float(d.max()),'passed':passed})
  report['comparisons']=comparisons
  if not all(c['passed'] for c in comparisons):raise RuntimeError('Persistent measured columns differ from adjacent original control')
  report['motor_after']=stationary();positions=report['motor_before']+report['motor_after']
  if max(positions)-min(positions)>.1:raise RuntimeError('Motor moved during stationary benchmark')
  times=[b['elapsed_s'] for b in report['batches'][1:]]
  report.update(status='stationary_persistent_speed_quality_passed',mean_persistent_batch_s=float(np.mean(times)),
       maximum_persistent_batch_s=max(times),batch_speedup=report['control_batch_s']/np.mean(times),
       motor_span_deg=max(positions)-min(positions))
 except BaseException as error:report.update(status='failed',error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
 finally:
  if camera is not None:report['interrupted_camera_cleanup']=scan.shutdown_camera(camera)
  if motor is not None:report['readonly_UART_transactions']=motor.transactions;motor.close()
  if runtime is not None:
   report.update(diagnostics=runtime.driver.diagnostics(),driver_healthy=runtime.driver.healthy,
      quarantined_buffers=runtime.driver.quarantined_buffer_count,same_overlay=runtime.read_identity()==runtime.identity)
   if not runtime.driver.healthy or runtime.driver.quarantined_buffer_count:report.update(status='recovery_required',recovery_hold=True)
   else:
    try:scan.assert_idle(report['diagnostics'],completed=counter,require_completed=True)
    except Exception as error:report.update(status='failed',idle_error=str(error))
  report['boot_id_after']=Path('/proc/sys/kernel/random/boot_id').read_text().strip();save()
  if report.get('recovery_hold'):
   while True:time.sleep(1)
  for f in reversed(locks):f.close()
 print(json.dumps({k:v for k,v in report.items() if k not in ['batches','readonly_UART_transactions','traceback']}));return 0 if report['status']=='stationary_persistent_speed_quality_passed' else 1
if __name__=='__main__':raise SystemExit(main())
