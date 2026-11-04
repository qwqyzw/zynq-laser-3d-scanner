"""Managed Zynq paired-exposure10deg scan, called by the bound74 BTN0 service.

Uses frozen61 geometry/motor/PL and individually reviewed actual5/20 PL data.
XYZ always stays on Zynq; the PC receives an explicitly experimental preview.
"""
from pathlib import Path
import argparse,json,hashlib,os,sys,time,uuid,csv,subprocess,signal,traceback
P=Path(__file__).resolve().parent.parent;D=Path(__file__).resolve().parent;R=P/'61_current_axis_laser_release_20261024';B=Path('/home/xilinx/jupyter_notebooks/button_scan')
VERSION='20261024.foreground_button.v1'
SOURCE_NAMES=['optimized_dual_scan.py','fuse_measured_profiles.py','diagnostic_camera.py','switchable_pl_camera.py','locked_switchable_camera_worker.py','temporal_consensus.py','roi_integer.py','foreground_select.py']
FIELDS=['point_index','profile_index','x_px','y_px','motor_position_deg','angle_from_geometry_zero_deg','x_camera_mm','y_camera_mm','z_camera_mm','x_mm','y_mm','z_mm','height_above_reference_mm','axis_radius_mm','source_exposure']
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p,obj):
 p=Path(p);t=p.with_name(p.name+'.writing');t.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False));t.replace(p)
def plan(step=10):
 if type(step) is not int or step!=10:raise ValueError('This managed dual profile is explicitly10deg')
 angles=list(range(0,361,step));moves=[j for a,b in zip(angles,angles[1:]) for j in range(a+5,b+1,5)]
 assert len(moves)==72 and moves[-1]==360
 return {'schema':'paired_actual_PL10deg_v1','angles_deg':angles,'finite_moves_deg':moves,'rpm':1,'max_transmitted_step_deg':5.5,'camera_batches':len(angles)*2,'actual_PL_transactions':len(angles)*6,'exposures':[5,20],'forward_span_deg':360,'reverse_return':False,'max_camera_batches':75}
def run(client_date,run_root,session_id,manifest_pin):
 import platform,shutil,fcntl
 if platform.machine() not in ['armv7l','aarch64']:raise RuntimeError('Actual scan requires Zynq')
 os.environ['XILINX_XRT']='/usr';os.environ['BOARD']='Pynq-Z2';sys.path[:0]=[str(D),str(R),str(P/'09_tools'),str(P/'04_runtime')]
 import cv2,numpy as np
 cv2.setNumThreads(1)
 import object_board_scan_360 as acq
 import fast_reconstruct_on_zynq as recon
 from fast_button_service import inspect_config
 from switchable_pl_camera import SwitchablePLCamera
 from fuse_measured_profiles import fuse_measured_profiles
 from fullturn_closure import compare_endpoints
 from run_integrated_board import retain_until_quiescent
 from release85 import verify_release
 binding=verify_release(manifest_pin)
 benchmark=load(P/'65_side_signal_optimization_20261024/locked_switch_benchmark_01/report.json')
 if benchmark['status']!='benchmark_passed' or not all(benchmark['checks'].values()):raise RuntimeError('Independent locked rapid-switch benchmark has not passed')
 route=plan();root=Path(run_root).resolve();rid=root.name;sid=session_id;assert root.parent==(B/'runs').resolve() and len(rid)==32 and len(sid)==32 and all(c in '0123456789abcdef' for c in rid+sid); F=root/'scan';M=root/'model';F.mkdir(parents=True);M.mkdir();source_hashes={n:digest(D/n) for n in SOURCE_NAMES};geom_hash=digest(R/'current_geometry.json')
 report={'schema':'paired_actual_PL_trial_v2','client_date':client_date,'version':VERSION,'run_id':rid,'session_id':sid,'status':'starting','object_from_user':'triangular pyramid','plan':route,'geometry_sha256':geom_hash,'source_sha256':source_hashes,'poses':[],'coordinate_producer':'zynq','metric_accuracy_accepted':False,'full_object_coverage_verified':False,'production_default_changed':True,'release_manifest_sha256':manifest_pin,'temporal_consensus_enabled':True,'extraction_policy':{'threshold':12,'PL_min_run':0,'PS_mask_min_run':4,'baseline_min_run8_preserved':True,'common_three_frames_minimum':10},'cleanup':{},'stage_timing_s':{'switch':0.,'capture_PL_reference':0.,'duplicate_evidence_check':0.,'geometry':0.,'motor':0.}}
 save(D/'current_dual_trial.json',{'run_id':rid,'session_id':sid,'directory':str(root),'report':str(root/'trial.json')});save(root/'trial.json',report)
 snap=root/'tested_sources';snap.mkdir()
 for n in SOURCE_NAMES:shutil.copyfile(D/n,snap/n)
 runtime=motor=camera=gpio=None;locks=[];closed=True;motor_closed=True;stopped=False;safe=False;cancelled=False;published=False;all_rows=[];short_rows=[];baseline_rows=[];expected=None;origin=None;began=time.monotonic()
 def checkpoint(state=None):
  if state:report['status']=state
  report['elapsed_s']=time.monotonic()-began
  if motor is not None:report['motion_log']=motor.motion_log
  save(root/'trial.json',report)

 def abort_check():
  if (F/'abort.request').exists() or (gpio is not None and int(gpio.read(0))&2):raise acq.AbortRequested('User cancelled paired exposure trial')
 def interrupted(signum,frame):(F/'abort.request').touch()
 signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
 def witness(phase):
  ref=cv2.imread('/home/xilinx/jupyter_notebooks/axis_correction_20261024_01/capture_v2/angle_000/bright_01.png',0);im=cv2.imread(str(F/'camera_session'/phase/'bright_01.png'),0);assert ref is not None and im is not None;parts=[]
  for x0,y0,x1,y1 in [(130,155,174,194),(405,155,445,192),(70,376,112,396),(466,376,510,397)]:
   scores=cv2.matchTemplate(im[y0-8:y1+8,x0-8:x1+8],ref[y0:y1,x0:x1],cv2.TM_CCOEFF_NORMED);_,corr,_,loc=cv2.minMaxLoc(scores);x,y=loc;rest=scores.copy();rest[max(0,y-3):y+4,max(0,x-3):x+4]=-1;margin=corr-float(rest.max());shift=float(np.hypot(x-8,y-8));parts.append({'correlation':corr,'shift_px':shift,'peak_margin':margin,'passed':bool(corr>=.9 and margin>=.02 and shift<=2)})
  return {'all_passed':all(p['passed'] for p in parts),'patches':parts}
 def actual_data(folder,batch,mods):
  import roi_integer as integer_model
  images=[];stripes=[];raw_record=load(folder/'capture.json')
  for idx,row in enumerate(raw_record['frames']):
   assert row['file']=='raw_%02d.png'%idx and digest(folder/row['file'])==row['sha256']
   locked=load(F/'camera_session/session_start.json')['original_controls']
   assert row['controls_readback']['ae']==1 and row['controls_readback']['exp']==batch['exposure'] and row['controls_readback']['awb']==0
   assert row['controls_readback']['wb']==locked['wb'] and row['controls_readback']['gain']==locked['gain']
   im=cv2.imread(str(folder/row['file']));words=np.fromfile(folder/('pl_%02d_actual.bin'%idx),dtype='<u4');assert words.shape==(640,) and np.array_equal(words,integer_model.extract_integer(im,tuple(cfg['roi']),3072,0))
   from laser_vertical_fast import keep_long_runs
   valid=keep_long_runs(words>>31!=0,4);stripes.append({'valid':valid,'y':np.where(valid,(words&0x3ffff)/256.,np.nan)});images.append(im)
  from temporal_consensus import review_from_stripes_fast
  pixels,quality=review_from_stripes_fast(images,stripes,tuple(cfg['roi']));quality.update(min_run=4,PL_min_run=0,run_filter_producer='zynq_PS_mask_on_actual_PL_centroids',unchanged_three_frame_common10=True);assert quality==load(folder/'stripe_quality.json')==batch['stripe_quality']
  saved=np.loadtxt(folder/'stripe_pixels.csv',delimiter=',',skiprows=1).reshape(-1,2);assert np.array_equal(pixels,saved)
  from laser_vertical_fast import keep_long_runs
  baseline_stripes=[{'valid':keep_long_runs(s['valid'],8),'y':s['y']} for s in stripes]
  baseline_pixels,baseline_quality=review_from_stripes_fast(images,baseline_stripes,tuple(cfg['roi']))
  return pixels,quality,baseline_pixels,baseline_quality
 def make_row(index,profile_index,pixels,profile,exposure,absolute):
  i=profile_index;vals=list(pixels[i])+[absolute,profile['angle_deg']]+list(profile['camera_xyz'][i])+list(profile['table_xyz'][i])+[profile['height'][i],profile['radius'][i]]
  assert np.isfinite(vals).all()
  return dict(zip(FIELDS,[index,i]+list(map(float,vals))+[exposure]))
 # Acquisition and publication below keep the same finite motor/DMA ownership.
 try:
  current=load(B/'status.json');assert current.get('run_id')==rid and current.get('session_id')==sid and current.get('child_pid')==os.getpid(),'Managed scan identity differs'
  cfg,art=inspect_config(B/'config_current_axis_20261024.json');assert digest(R/'SHA256.json')=='33b50a6ecf2c98923f078506a45cee8ef4d6f391b3ecc64e307c057e30a334db';assert load(R/'operator_clearance.json')['user_confirmed_full_turn_clearance'] is True
  assert subprocess.run(['fuser','/dev/video0'],capture_output=True).returncode==1
  for n in ['laser_motor_object_scan.lock']:
   f=open('/tmp/'+n,'a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
  hw=acq.load_modules(P,board=True);runtime=acq.attach_loaded_runtime(P,hw,art);expected=runtime.initial_diagnostics['completed_frames'];report['baseline_PL_counter']=expected;report['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip();report['overlay']=runtime.identity
  from pynq import MMIO
  abort_check()  # Parent owns GPIO/debounce; child honors the bounded abort file.
  class TrialMotor(acq.FiniteEnvelopeMixin,hw['motor_uartlite'].UARTLiteAngleController):
   def _exchange(self,operation,plan=None):
    if operation in [1,2]:abort_check()
    return super()._exchange(operation,plan)
   def status(self):abort_check();return super().status()
   def position(self):abort_check();return super().position()
  motor=TrialMotor(runtime.overlay,address=1);motor_closed=False;report['stationary_before']=acq.read_motor_stationary(motor,F);mods=recon.modules(P);model=load(R/'current_geometry.json');identity=mods.board_pointcloud_export.board_identity();assert identity['is_linux_arm_zynq_or_pynq'];report['execution_platform']=identity
  camera=SwitchablePLCamera((D/'locked_switchable_camera_worker.py').read_text(),cfg['roi'],runtime.driver,verify_ps=True,exposure=5);closed=False;report['camera_ready']=camera.start(F);report['fixed_before']=witness('bright_before_session');assert report['fixed_before']['all_passed'];checkpoint('acquiring');last=0
  for index,angle in enumerate(route['angles_deg']):
   abort_check()
   if index:
    for step in range(last+5,angle+1,5):
     abort_check();stage_start=time.monotonic();moved=motor.move_to(origin+step,rpm=1);acq.assert_motor_state(motor.status(),moved['motor_position_deg'],origin,360);report['stage_timing_s']['motor']+=time.monotonic()-stage_start
    last=angle
   point={'index':index,'nominal_deg':angle,'exposures':[]};report['poses'].append(point);parts={};profiles={};baseline_profiles={};absolute_positions={};checkpoint()
   for exposure in [5,20]:
    abort_check();assert runtime.read_identity()==runtime.identity;acq.assert_idle(runtime.driver.diagnostics(),completed=expected,require_completed=expected!=report['baseline_PL_counter']);stage_start=time.monotonic();sw=camera.configure_exposure(exposure);report['stage_timing_s']['switch']+=time.monotonic()-stage_start;before=motor.position()['motor_position_deg'];T=F/('point_%03d/exp_%d/images'%(index,exposure));stage_start=time.monotonic();batch=camera.capture(T);report['stage_timing_s']['capture_PL_reference']+=time.monotonic()-stage_start;after=motor.position()['motor_position_deg'];assert abs(after-before)<=.1
    transactions=load(T/'pl_transactions.json');assert [r['completed_frames'] for r in transactions]==[(expected+i)&0xffffffff for i in [1,2,3]];expected=(expected+3)&0xffffffff;acq.assert_idle(runtime.driver.diagnostics(),completed=expected,require_completed=True)
    stage_start=time.monotonic();pixels,q,bp,bq=actual_data(T,batch,mods);report['stage_timing_s']['duplicate_evidence_check']+=time.monotonic()-stage_start;stage_start=time.monotonic();profile=recon.reconstruct_profile(pixels,before,model,mods,diagnostic_preview=True) if len(pixels) and q['image_quality_gate_passed'] else None;baseline_profile=recon.reconstruct_profile(bp,before,model,mods,diagnostic_preview=True) if len(bp) and bq['image_quality_gate_passed'] else None;report['stage_timing_s']['geometry']+=time.monotonic()-stage_start
    selected=profile['selected'] if profile is not None else np.zeros(len(pixels),bool);parts[exposure]={'pixels':pixels,'selected':selected,'quality_gate_passed':q['image_quality_gate_passed'],'temporal_columns':[r['x'] for r in q.get('two_frame_support',[])]};profiles[exposure]=profile;absolute_positions[exposure]=before
    parts[exposure]['baseline']={'pixels':bp,'selected':baseline_profile['selected'] if baseline_profile is not None else np.zeros(len(bp),bool),'quality_gate_passed':bq['image_quality_gate_passed'],'temporal_columns':[r['x'] for r in bq.get('two_frame_support',[])]};baseline_profiles[exposure]=baseline_profile
    point['exposures'].append({'exposure':exposure,'before_deg':before,'after_deg':after,'quality':q,'baseline_quality':bq,'baseline_object_columns':int(parts[exposure]['baseline']['selected'].sum()),'object_columns':int(selected.sum()),'PL_counter':expected,'switch_receipt':sw,'evidence':str(T.relative_to(root))});checkpoint()
   assert abs(absolute_positions[5]-absolute_positions[20])<=.1
   fused,fr=fuse_measured_profiles(parts[5],parts[20]);point['fusion']=fr;point['fused_points']=len(fused);point['motor_position_before_capture_deg']=absolute_positions[5]
   for i in np.flatnonzero(parts[5]['baseline']['selected']):short_rows.append(make_row(index,int(i),parts[5]['baseline']['pixels'],baseline_profiles[5],5,absolute_positions[5]))
   for f in fused:
    e=f['source_exposure'];old=f.get('source_tier')=='baseline_min_run8';measured=make_row(index,f['source_index'],parts[e]['baseline']['pixels'] if old else parts[e]['pixels'],baseline_profiles[e] if old else profiles[e],e,absolute_positions[e]);all_rows.append(measured)
    if old:baseline_rows.append(measured)
   point['status']='completed';checkpoint();print(json.dumps({'pose':index+1,'angle':angle,'short':point['exposures'][0]['object_columns'],'fused':len(fused),'PL':expected}),flush=True)
   if origin is None:
    assert len(fused)>=10,'Insufficient actual object columns at preflight';ready=motor.prepare();origin=float(ready['initial_position_deg']);motor.arm_object_envelope(origin,360);report['motor_origin_deg']=origin
  assert len(report['poses'])==37 and motor.fd_attempts==72 and expected==(report['baseline_PL_counter']+222)&0xffffffff
  report['cleanup']['motor_stop']=motor.stop();report['cleanup']['motor_stop_confirmed']=True;motor.close();motor_closed=True
  report['cleanup']['camera']=camera.close();closed=True;report['fixed_after']=witness('bright_after_session');assert report['fixed_after']['all_passed'];report['final_diagnostics']=runtime.driver.diagnostics();acq.assert_idle(report['final_diagnostics'],completed=expected,require_completed=True);assert runtime.driver.healthy and runtime.driver.quarantined_buffer_count==0;safe=True
  checkpoint('reconstructing_on_zynq');abort_check()
  # This selection never changes coordinates; preserve every raw candidate.
  from foreground_select import select_foreground
  baseline_ids={id(row) for row in baseline_rows};baseline_mask=np.array([id(row) in baseline_ids for row in all_rows],bool)
  measured=np.array([[row[k] for k in ['x_mm','y_mm','z_mm']] for row in all_rows]);groups=np.array([row['point_index']%36 for row in all_rows]);radii=np.array([row['axis_radius_mm'] for row in all_rows])
  for name,rows in [('all_candidate_points.csv',all_rows),('all_min8_baseline_candidates.csv',baseline_rows)]:
   with (M/name).open('x',newline='') as f:w=csv.DictWriter(f,FIELDS);w.writeheader();w.writerows(rows)
  keep,selection=select_foreground(measured,groups,radii,baseline_mask)
  report['raw_candidate_point_count']=len(all_rows);report['foreground_selection']=selection;all_rows=[row for row,k in zip(all_rows,keep) if k]
  object_baseline=[row for row in all_rows if id(row) in baseline_ids]
  with (M/'baseline_min8_points.csv').open('x',newline='') as f:w=csv.DictWriter(f,FIELDS);w.writeheader();w.writerows(object_baseline)
  report['baseline_min8_csv_sha256']=digest(M/'baseline_min8_points.csv')
  for point in report['poses']:
   point['raw_candidate_fused_points']=point['fused_points'];point['fused_points']=sum(row['point_index']==point['index'] for row in all_rows)
  checkpoint()

  for name,rows in [('object_points.csv',all_rows),('baseline_short_points.csv',short_rows)]:
   with (M/name).open('x',newline='') as f:w=csv.DictWriter(f,FIELDS);w.writeheader();w.writerows(rows)
  endpoints=[]
  for idx in [0,36]:endpoints.append({int(r['x_px']):(r['x_mm'],r['y_mm'],r['z_mm'],r['y_px']) for r in all_rows if r['point_index']==idx})
  closure=compare_endpoints(*endpoints);span=report['poses'][-1]['motor_position_before_capture_deg']-report['poses'][0]['motor_position_before_capture_deg'];closure['feedback_span_deg']=span;assert abs(span-360)<=.4 and closure['passed'],'Measured endpoint closure failed';report['closure']=closure
  assert all(digest(D/n)==h for n,h in source_hashes.items()) and digest(R/'current_geometry.json')==geom_hash;inspect_config(B/'config_current_axis_20261024.json');abort_check()
  metadata={'title':'双曝光按键版 · 10°实测点云（含多帧支持）','units':'mm','axis_units':dict.fromkeys('xyz','mm'),'data_origin':'hardware_scan','coordinate_producer':'zynq','synthetic':False,'diagnostic_preview_only':True,'alignment_validated':False,'metric_accuracy_accepted':False,'geometry_status':'双曝光实验版，使用当前标定；表面完整性与毫米精度未验收','coverage_note':'每10度采集5和20两档曝光，各3帧实际PL；保持三帧共同10列门槛，额外保留一致的两帧连续段；冲突拒绝、无插值。按高度≥3mm、轴距≤60mm筛选。','software_version':VERSION,'frozen61_manifest_sha256':digest(R/'SHA256.json'),'geometry_sha256':geom_hash,'source_sha256':source_hashes,'client_date':client_date,'object_acquisition_pose_count':37,'nominal_scan_span_deg':360,'acquisition_step_deg':10,'fullturn_closure':closure,'full_object_coverage_verified':False,'source_images':[{'path':str(F/'camera_session/bright_after_session/bright_01.png'),'label':'本轮双曝光整圈结束亮图'}]}
  save(M/'object_metadata.json',metadata);report['export']=mods.board_pointcloud_export.export(M/'object_points.csv',M/'object_metadata.json',M/'viewer');abort_check()
  assert all(digest(D/n)==h for n,h in source_hashes.items()) and digest(R/'current_geometry.json')==geom_hash
  manifest=digest(M/'viewer/export_manifest.json');report['summary']={'points':len(all_rows),'baseline_short_points':len(short_rows),'empty_short_angles_deg':[p['nominal_deg'] for p in report['poses'] if p['exposures'][0]['object_columns']==0],'empty_fused_angles_deg':[p['nominal_deg'] for p in report['poses'] if p['fused_points']==0],'height_min_mm':min(r['height_above_reference_mm'] for r in all_rows),'height_max_mm':max(r['height_above_reference_mm'] for r in all_rows)}
  latest={'schema_version':1,'session_id':sid,'run_id':rid,'state':'ready','result_kind':'diagnostic_preview','diagnostic_preview_only':True,'alignment_validated':False,'metric_accuracy_accepted':False,'viewer_relative':'runs/'+rid+'/model/viewer','export_manifest_sha256':manifest,'point_count':len(all_rows),'experimental_profile':VERSION};report['proposed_result']=latest;published=True;checkpoint('ready_diagnostic_preview')
 except BaseException as e:
  cancelled=isinstance(e,acq.AbortRequested);report['status']='cancelled' if cancelled else 'failed';report['error']=str(e);report['traceback']=traceback.format_exc()[-2200:];checkpoint()
 finally:
  if camera is not None and not closed:
   try:report['cleanup']['camera']=camera.close();closed=True
   except Exception as e:report['cleanup']['camera_error']=str(e)
  if motor is not None and not motor_closed:
   try:report['cleanup']['motor_stop']=motor.stop();report['cleanup']['motor_stop_confirmed']=True
   except Exception as e:report['cleanup']['motor_stop_error']=str(e)
   try:motor.close();motor_closed=True
   except Exception as e:report['cleanup']['motor_close_error']=str(e)
  if runtime is not None:
   report['final_diagnostics']=runtime.driver.diagnostics();report['driver_healthy']=runtime.driver.healthy;report['quarantined_buffers']=runtime.driver.quarantined_buffer_count
  checkpoint()
  if runtime is not None:retain_until_quiescent(runtime,root/'trial.json',report)
  safe=bool(closed and motor_closed and report['cleanup'].get('motor_stop_confirmed') and runtime is not None and runtime.driver.healthy and runtime.driver.quarantined_buffer_count==0)
  if safe:
   try:acq.assert_idle(runtime.driver.diagnostics())
   except Exception as e:safe=False;report['cleanup']['idle_error']=str(e)
  if safe:
   for f in reversed(locks):f.close()
  report['managed_child_complete']=safe

  if root.exists():save(root/'trial.json',report)
  save(D/'last_optimized_trial_summary.json',{k:v for k,v in report.items() if k not in ['poses','motion_log','stationary_before','export']})
  if not safe:
   while True:time.sleep(1)
 return report
if __name__=='__main__':
 parser=argparse.ArgumentParser(description='Managed dual-exposure worker; service owns GPIO and result publication')
 parser.add_argument('--run-root',type=Path,required=True);parser.add_argument('--session-id',required=True)
 parser.add_argument('--manifest-sha256',required=True);parser.add_argument('--client-date',required=True)
 args=parser.parse_args()
 from datetime import date
 if date.fromisoformat(args.client_date).isoformat()!=args.client_date:parser.error('Explicit client date required')
 result=run(args.client_date,args.run_root,args.session_id,args.manifest_sha256)
 print(json.dumps({k:v for k,v in result.items() if k in ['status','run_id','summary','error','elapsed_s']},indent=2))
 raise SystemExit(0 if result['status']=='ready_diagnostic_preview' else 1)
