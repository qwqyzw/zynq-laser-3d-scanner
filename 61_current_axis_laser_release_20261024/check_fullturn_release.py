"""Full-turn route/command/failure/closure checks using explicitly synthetic fixtures."""
from pathlib import Path
import ast,copy,json,sys,tempfile,unittest
from unittest.mock import patch
R=Path(__file__).resolve().parent;P=R.parent
sys.path.insert(0,str(R));sys.path.insert(0,str(P/'09_tools'))
import fast_release as release
import fast_button_service as service
import fast_reconstruct_on_zynq as recon
import object_board_scan_360 as scan
from fullturn_closure import compare_endpoints

def functions(path):
    return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_text(encoding='utf-8-sig')).body
            if isinstance(n,ast.FunctionDef)}

class Checks(unittest.TestCase):
    def test_all_bindings_and_original_geometry(self):
        verified=release.verify_release(P,R,release.digest(R/'SHA256.json'))
        self.assertFalse(verified['hardware_accessed']);self.assertFalse(verified['profile']['full_axis_validation_passed'])
        self.assertEqual((R/'current_geometry.json').read_bytes(),(P/'30_current_geometry_release_20261021/current_geometry.json').read_bytes())
    def test_real_global_full_turn_plan_no_wrapping(self):
        p=scan.nominal_plan(360)
        self.assertEqual(p['acquisition_angles_deg'],list(range(0,361,5)))
        self.assertEqual(p['return_angles_deg'],list(range(355,-1,-5)))
        self.assertEqual(p['expected_original_images'],675);self.assertEqual(p['expected_new_PL_transactions'],225)
        self.assertEqual(p['maximum_FD_attempts'],144)
        self.assertEqual(p['route_audit']['nominal_total_travel_deg'],720)
        stages=p['stage_records'];self.assertEqual(len(stages),6)
        origin=17.25
        bound=[{**s,'global_origin_motor_deg':origin,'absolute_targets_deg':[origin+x for x in s['global_angles_deg']]} for s in stages]
        scan.validate_object_route(origin,p['acquisition_angles_deg'],p['return_angles_deg'],stage_records=bound)
        bound[-1]['absolute_targets_deg']=[origin+x for x in bound[-1]['local_angles_deg']]
        with self.assertRaises(ValueError):scan.validate_object_route(origin,p['acquisition_angles_deg'],p['return_angles_deg'],stage_records=bound)
    def test_no_unbounded_route_or_long_return(self):
        for angle in (True,365,720,-360,359):
            with self.subTest(angle=angle),self.assertRaises(ValueError):scan.nominal_plan(angle)
        p=scan.nominal_plan(360)
        with self.assertRaises(ValueError):scan.validate_object_route(0,p['acquisition_angles_deg'],[0])
        with self.assertRaises(ValueError):scan.validate_object_route(0,list(range(0,366,5)),list(range(360,-1,-5)))
    def test_finite_envelope_rejects_wrap_long_step_and_reenable(self):
        from types import SimpleNamespace
        class Sink:
            def __init__(self):self.prepared=True;self.geometry=SimpleNamespace(degrees_per_pulse=.1125);self.sent=[]
            def _exchange(self,operation,plan=None):self.sent.append((operation,plan))
        M=type('SyntheticMotor',(scan.FiniteEnvelopeMixin,Sink),{})
        motor=M();motor.arm_object_envelope(0,360)
        plan={'quantized_delta_deg':4.95,'quantized_target_motor_deg':359.95,'requested_target_motor_deg':360,
              'rpm':1,'direction':0,'pulses':44}
        motor._exchange(2,plan);self.assertEqual(len(motor.sent),1)
        for field,value in [('rpm',2),('quantized_target_motor_deg',365),('requested_target_motor_deg',365),('pulses',100)]:
            bad=dict(plan);bad[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):motor._exchange(2,bad)
        with self.assertRaises(RuntimeError):motor.arm_object_envelope(360,360)
        with self.assertRaises(RuntimeError):motor._exchange(1)
        motor.fd_attempts=144
        with self.assertRaises(RuntimeError):motor._exchange(2,plan)
    def test_transaction_loop_and_cleanup_original_ast(self):
        old=functions(P/'09_tools/object_board_scan.py');new=functions(R/'object_board_scan_360.py')
        for name in ('run_acquisition','execute_board','verify_capture','assert_idle','attach_loaded_runtime',
                     'read_motor_stationary','assert_motor_state','shutdown_camera'):
            self.assertEqual(new[name],old[name],name)
        old=functions(P/'30_current_geometry_release_20261021/fast_reconstruct_on_zynq.py')
        new=functions(R/'fast_reconstruct_on_zynq.py')
        for name in ('reconstruct_profile','validate_scan','verify_batch_files','actual_pl_pixels','capture_groups','unchanged'):
            self.assertEqual(old[name],new[name],name)
    def test_optical_endpoint_closure_pass_and_reject_synthetic_only(self):
        first={i:(float(i),0.,10.,290.) for i in range(30)}
        good={i:(float(i)+.2,.1,10.1,290.3) for i in range(30)}
        self.assertTrue(compare_endpoints(first,good)['passed'])
        bad={i:(float(i)+8.,0.,10.,295.) for i in range(30)}
        self.assertFalse(compare_endpoints(first,bad)['passed'])
        self.assertFalse(compare_endpoints(first,{i:good[i] for i in range(19)})['passed'])
        bad[1]=(float('nan'),0.,0.,0.)
        with self.assertRaises(ValueError):compare_endpoints(first,bad)
    def test_config_start_routes_both_new_executables(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);path=folder/'config.json'
            value={'project':str(P),'overlay_release':str(P/'17_button_scan_release_20261020'),
                'geometry':str(R/'current_geometry.json'),'geometry_sha256':release.GEOMETRY,'output_root':str(folder/'out'),
                'client_date':'2026-10-21','mode':'diagnostic_preview','roi':[175,270,410,320],'exposure':5,'max_angle':360,
                'fast_release':str(R),'fast_manifest_sha256':release.digest(R/'SHA256.json')}
            path.write_text(json.dumps(value));cfg,art=service.inspect_config(path);cfg['config_file']=path
            c=service.Coordinator(cfg,art)
            with patch.object(c,'launch') as launch:c.start()
            args,stage=launch.call_args.args
            routed=service.route_args(args,stage,cfg,value['fast_manifest_sha256'])
            self.assertEqual(routed[3],str(R/'object_board_scan_360.py'));self.assertIn('--fast-manifest-sha256',routed)
            self.assertEqual(routed[routed.index('--max-angle')+1],'360')
            args=[sys.executable,'-B','-u',str(P/'09_tools/reconstruct_object_on_zynq.py'),'--run-board','--diagnostic-preview']
            self.assertEqual(service.route_args(args,'reconstructing_on_zynq',cfg,value['fast_manifest_sha256'])[3],str(R/'fast_reconstruct_on_zynq.py'))
            value['max_angle']=60;path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):service.inspect_config(path)
    def test_original_button_stop_and_poll(self):
        for name in ('attach','poll','serve','request_stop','finish','status'):
            self.assertIs(getattr(service.Coordinator,name),getattr(service.original_service.Coordinator,name))
    def test_pc_real_xyz_refused(self):
        from types import SimpleNamespace
        fake=SimpleNamespace(board_pointcloud_export=SimpleNamespace(board_identity=lambda:{'is_linux_arm_zynq_or_pynq':False}))
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'forbidden'
            with self.assertRaisesRegex(ValueError,'actual Zynq device tree'):
                recon.reconstruct_board(P,Path(tmp),R/'current_geometry.json',output,fake,diagnostic_preview=True,
                                        fast_manifest_sha256=release.digest(R/'SHA256.json'))
            self.assertFalse(output.exists())

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    summary={'all_passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
             'actual_hardware_test_performed':False,'real_XYZ_computed_on_PC':False,
             'manifest_sha256':release.digest(R/'SHA256.json')}
    (P/'results/current_calibration_20261021/fullturn_offline_checks.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary));raise SystemExit(0 if result.wasSuccessful() else 1)
