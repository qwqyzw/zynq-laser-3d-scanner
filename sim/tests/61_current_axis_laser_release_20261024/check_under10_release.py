"""Simulated planning, loop, failure, binding and schema tests. No real XYZ."""
from pathlib import Path
import ast,json,sys,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
R=Path(__file__).resolve().parent;P=R.parent
sys.path.insert(0,str(R));sys.path.insert(0,str(P/'09_tools'))
import fast_release as release
import fast_button_service as service
import object_board_scan_360 as scan
import fast_reconstruct_on_zynq as recon
from check_object_board_scan import FakeCamera,FakeDriver,FakeMotorBase
from fullturn_closure import compare_endpoints

def functions(path):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef)}
class Checks(unittest.TestCase):
 def test_bound_identity_and_unchanged_current_geometry(self):
  binding=release.verify_release(P,R,release.digest(R/'SHA256.json'))
  self.assertEqual(binding['profile']['original_images_per_scan'],225)
  self.assertEqual(binding['profile']['camera_open_count_per_scan'],1)
  self.assertFalse(binding['profile']['unwrapped_feedback_rezeroed'])
  self.assertEqual((R/'current_geometry.json').read_bytes(),(P/'32_fullturn_release_20261021/current_geometry.json').read_bytes())
 def test_full_turn_without_reverse_or_origin_reset(self):
  plan=scan.nominal_plan(360)
  self.assertEqual(plan['acquisition_angles_deg'],list(range(0,361,5)));self.assertEqual(plan['return_angles_deg'],[])
  self.assertEqual(plan['maximum_FD_attempts'],72);self.assertEqual(plan['expected_new_PL_transactions'],225)
  self.assertEqual(plan['expected_original_images'],225)
  self.assertEqual(plan['route_audit']['nominal_total_travel_deg'],360)
  with self.assertRaises(ValueError):scan.nominal_plan(60)
  with self.assertRaises(ValueError):scan.validate_object_route(0,plan['acquisition_angles_deg'],[0])
 def test_injected_complete_and_failure_sessions(self):
  for case in ['complete','quality_failure','movement_failure','abort']:
   with self.subTest(case=case),tempfile.TemporaryDirectory(prefix='SIMULATED_UNDER10_') as tmp:
    folder=Path(tmp);trace=[];driver=FakeDriver();M=type('SimulatedMotor',(scan.FiniteEnvelopeMixin,FakeMotorBase),{})
    motor=M(trace);camera=FakeCamera(trace,driver);identity={'sha256':'SIMULATED','fclk0_mhz':50}
    runtime=SimpleNamespace(driver=driver,identity=identity,initial_diagnostics=driver.diagnostics(),read_identity=lambda:identity.copy())
    if case=='quality_failure':camera.fail_capture=42
    if case=='movement_failure':motor.fail_move=37
    if case=='abort':
     def abort(n):
      if n==39:(folder/'abort.request').touch()
     camera.on_capture=abort
    with patch.object(scan,'progress'),patch.object(scan.time,'sleep'):
     report=scan.run_acquisition(folder,runtime,motor,camera,scan.nominal_plan(360),client_date='2026-10-21',roi=(175,270,410,320),exposure=5,hardware=False)
    self.assertTrue(report['simulation']);self.assertFalse(report['real_hardware_acquisition_verified'])
    self.assertTrue(report['cleanup']['motor_stop_confirmed']);self.assertTrue(motor.closed)
    self.assertEqual(report['return_points'],[])
    if case=='complete':
     self.assertEqual(report['status'],'completed');self.assertEqual(motor.fd_attempts,72)
     self.assertEqual(camera.calls,75);self.assertEqual(driver.count,68+225)
     self.assertTrue(report['forward_full_turn_completed']);self.assertFalse(report['return_to_origin_completed'])
     self.assertAlmostEqual(report['final_feedback_offset_deg'],360,delta=.1)
     self.assertEqual(motor.prepare_calls,1)
     self.assertEqual(len(report['bound_absolute_targets_deg']),73)
    else:self.assertIn(report['status'],['failed','aborted']);self.assertFalse(report['automatic_return_after_failure'])
 def test_original_pixel_and_xyz_math(self):
  old=functions(P/'32_fullturn_release_20261021/fast_reconstruct_on_zynq.py');new=functions(R/'fast_reconstruct_on_zynq.py')
  for name in ['reconstruct_profile','actual_pl_pixels','unchanged','capture_groups']:
   self.assertEqual(old[name],new[name],name)
  old=functions(P/'09_tools/object_board_scan.py');new=functions(R/'object_board_scan_360.py')
  for name in ['verify_capture','assert_idle','assert_motor_state','attach_loaded_runtime','read_motor_stationary']:
   self.assertEqual(old[name],new[name],name)
 def test_bounded_movement_and_no_reenable(self):
  class Sink:
   def __init__(self):self.prepared=True;self.geometry=SimpleNamespace(degrees_per_pulse=.1125)
   def _exchange(self,operation,plan=None):return True
  M=type('SimulatedBounded',(scan.FiniteEnvelopeMixin,Sink),{});motor=M();motor.arm_object_envelope(0,360)
  p={'quantized_delta_deg':4.95,'quantized_target_motor_deg':359.95,'requested_target_motor_deg':360,'rpm':1,'direction':0,'pulses':44}
  self.assertTrue(motor._exchange(2,p))
  for key,value in [('rpm',3),('requested_target_motor_deg',365),('pulses',100)]:
   with self.subTest(key=key),self.assertRaises(RuntimeError):motor._exchange(2,{**p,key:value})
  motor.fd_attempts=72
  with self.assertRaises(RuntimeError):motor._exchange(2,p)
  with self.assertRaises(RuntimeError):motor.arm_object_envelope(360,360)
 def test_closure_still_rejects_shifted_object(self):
  a={i:(float(i),0.,10.,290.) for i in range(30)};b={i:(float(i)+.1,0.,10.,290.1) for i in range(30)}
  self.assertTrue(compare_endpoints(a,b)['passed'])
  self.assertFalse(compare_endpoints(a,{i:(float(i)+5,0.,10.,295.) for i in range(30)})['passed'])
 def test_new_config_routes_original_start_and_gpio_stop(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);path=folder/'config.json';pin=release.digest(R/'SHA256.json')
   cfg={'project':str(P),'overlay_release':str(P/'17_button_scan_release_20261020'),'geometry':str(R/'current_geometry.json'),
    'geometry_sha256':release.GEOMETRY,'output_root':str(folder/'out'),'client_date':'2026-10-21','mode':'diagnostic_preview',
    'roi':[175,270,410,320],'exposure':5,'max_angle':360,'fast_release':str(R),'fast_manifest_sha256':pin}
   path.write_text(json.dumps(cfg));config,art=service.inspect_config(path);config['config_file']=path
   c=service.Coordinator(config,art)
   with patch.object(c,'launch') as launch:c.start()
   args,stage=launch.call_args.args;routed=service.route_args(args,stage,config,pin)
   self.assertEqual(routed[3],str(R/'object_board_scan_360.py'))
   self.assertEqual(routed[routed.index('--max-angle')+1],'360')
  for name in ['poll','serve','attach']:
   self.assertIs(getattr(service.Coordinator,name),getattr(service.original_service.Coordinator,name))
 def test_compact_checkpoint_preserves_every_field(self):
  import importlib.util
  old=functions(P/'36_under10_fullturn_release_20261021/object_board_scan_360.py')
  new=functions(R/'object_board_scan_360.py')
  for name in old:
   if name!='save':self.assertEqual(old[name],new[name],name)
  original={'points':[{'index':i,'status':'completed','feedback':i*4.99,'raw':[{'sha256':'a'*64,'image':'raw_00.png'}]} for i in range(73)],
   'read_transactions':[{'rx':[1,2,3],'tx':[4,5,6],'passed':True} for i in range(200)],'note':'保留全部记录','cleanup':None}
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'completion.json';scan.save(p,original)
   self.assertEqual(json.loads(p.read_text()),original)
   self.assertLess(p.stat().st_size,len(json.dumps(original,ensure_ascii=False,indent=2).encode()))
 def test_previous_calibration_reuse_is_explicit_and_never_current_validation(self):
  model=json.loads((R/'current_geometry.json').read_text())
  evidence=recon.diagnostic_geometry_evidence(model)
  for data in [release.EXPECTED_PROFILE,evidence,recon.diagnostic_preview_metadata(model),release.verify_release(P,R,release.digest(R/'SHA256.json'))]:
   self.assertFalse(data['current_geometry_revalidated'])
  self.assertTrue(evidence['previous_calibration_checks_passed'])
  self.assertFalse(evidence['rough_alignment_checks_passed'])
  self.assertTrue(evidence['geometry_reuse_authorized'])
  self.assertTrue(evidence['historical_extrinsics_reused'])
  self.assertFalse(evidence['metric_accuracy_accepted'])
  self.assertIn('旧标定',recon.diagnostic_preview_metadata(model)['geometry_status'])
  auth=json.loads((R/'reuse_authorization.json').read_text());auth['skip_new_calibration']=False
  with patch.object(recon,'load',return_value=auth),self.assertRaises(ValueError):recon.diagnostic_geometry_evidence(model)
  for name in ['persistent_camera_worker.py','object_board_scan_360.py','fullturn_closure.py']:
   self.assertEqual((R/name).read_bytes(),(P/'38_compact_fullturn_release_20261022'/name).read_bytes())
 def test_integer_reference_and_quality_on_all_recorded_batches(self):
  try:import cv2,numpy as np
  except ImportError:self.skipTest('Recorded-image test requires OpenCV; run all tests on Zynq before deployment')
  from persistent_pl_camera import independent_integer_reference
  from quality_from_stripes_fast import review_from_stripes_fast
  from laser_horizontal_check import extract_horizontal
  root=P/'44_white_box_roi_20261022/raw_run_5b24f039df3840fca8dae0e5be41ebdb/scan'
  if not root.exists():root=Path('/home/xilinx/jupyter_notebooks/button_scan/runs/5b24f039df3840fca8dae0e5be41ebdb/scan')
  roi=(175,210,440,340);batches=0
  for folder in sorted(root.glob('*/images')):
   if not (folder/'pl_02_actual.bin').exists():continue
   images=[];actual=[];references=[]
   for i in range(3):
    image=cv2.imread(str(folder/('raw_%02d.png'%i)))
    words=np.fromfile(folder/('pl_%02d_actual.bin'%i),dtype='<u4')
    valid=words>>31!=0
    raw=extract_horizontal(image,roi,12,8)
    self.assertTrue(np.array_equal(raw['valid'],valid))
    self.assertLessEqual(float(np.max(abs(raw['y'][valid]-(words[valid]&0x3ffff)/256),initial=0)),.25)
    reference=independent_integer_reference(image,roi,words)
    images.append(image);actual.append({'valid':valid,'y':np.where(valid,(words&0x3ffff)/256,np.nan)});references.append(reference)
    if batches==0 and i==0:
     corrupt=words.copy();corrupt[np.flatnonzero(valid)[0]]^=np.uint32(1)
     with self.assertRaisesRegex(RuntimeError,'differs from actual PL'):independent_integer_reference(image,roi,corrupt)
   a,aq=review_from_stripes_fast(images,actual,roi);b,bq=review_from_stripes_fast(images,references,roi)
   self.assertEqual(aq,bq);self.assertTrue(np.array_equal(a,b));batches+=1
  self.assertEqual(batches,21)
 def test_cold_child_button_overlay_inspection(self):
  import subprocess,os
  env=dict(os.environ);env.pop('PYTHONPATH',None)
  with tempfile.TemporaryDirectory() as tmp:
   child=subprocess.run([sys.executable,'-B',str(R/'object_board_scan_360.py'),'--project',str(P),
    '--button-release',str(P/'17_button_scan_release_20261020'),'--fast-manifest-sha256',release.digest(R/'SHA256.json')],
    cwd=tmp,env=env,capture_output=True,text=True,timeout=40)
  self.assertEqual(child.returncode,0,child.stderr)
  self.assertFalse(json.loads(child.stdout)['hardware_accessed'])
 def test_pc_cannot_compute_real_xyz(self):
  fake=SimpleNamespace(board_pointcloud_export=SimpleNamespace(board_identity=lambda:{'is_linux_arm_zynq_or_pynq':False}))
  with tempfile.TemporaryDirectory() as tmp,self.assertRaisesRegex(ValueError,'actual Zynq device tree'):
   recon.reconstruct_board(P,Path(tmp),R/'current_geometry.json',Path(tmp)/'forbidden',fake,diagnostic_preview=True,fast_manifest_sha256=release.digest(R/'SHA256.json'))

if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
 out={'all_passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
      'hardware_accessed':False,'real_XYZ_computed_on_PC':False,'manifest_sha256':release.digest(R/'SHA256.json')}
 (P/'results/current_calibration_20261021/integer_quality_offline_checks.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
 raise SystemExit(0 if result.wasSuccessful() else 1)
