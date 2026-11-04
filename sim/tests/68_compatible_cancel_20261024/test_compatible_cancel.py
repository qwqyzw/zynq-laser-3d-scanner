import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock,mock_open
from compatible_cancel import inspect_cleanup,sha,CompatibleCancelMixin

class Checks(unittest.TestCase):
    def fixture(self,root):
        run=root/('a'*32);camera=run/'scan/camera_session';camera.mkdir(parents=True)
        worker=run/'scan/persistent_camera_worker.py';worker.write_text('# synthetic fixture')
        close={'camera_release_confirmed':True,'exposure_restoration_verified':True,'restore_errors':[],
               'error':None,'explicit_close_received':True,'worker_sha256':sha(worker)}
        receipt=camera/'session_close.json';receipt.write_text(json.dumps(close))
        cleanup={'motor_stop_confirmed':True,'camera':{'camera_release_confirmed':True,'no_active_capture_process':True,
            'exposure_restoration_verified':True,'session_close_sha256':sha(receipt)}}
        data={'schema':'paired_actual_PL_trial_v1','version':'20261024.dual_actual_PL.experimental.v1','run_id':run.name,
            'session_id':'b'*32,'status':'ready_diagnostic_preview','cleanup':cleanup,'driver_healthy':True,
            'quarantined_buffers':0,'final_diagnostics':{'status':2,'dma':{'mm2s_sr':4098,'s2mm_sr':4098}}}
        (run/'trial.json').write_text(json.dumps(data));return run,data
    def test_valid_trial_and_all_supported_final_states(self):
        for state in ['ready_diagnostic_preview','failed','cancelled']:
            with self.subTest(state=state),tempfile.TemporaryDirectory() as tmp:
                run,d=self.fixture(Path(tmp));d['status']=state;(run/'trial.json').write_text(json.dumps(d))
                self.assertEqual(inspect_cleanup(run)['kind'],'actual_PL65_trial')
    def test_valid_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            run,d=self.fixture(Path(tmp));(run/'scan/completion.json').write_text(json.dumps(d));(run/'trial.json').unlink()
            self.assertEqual(inspect_cleanup(run)['kind'],'legacy61_completion')
    def test_missing_schema_bad_identity_busy_DMA_and_unsafe_final_state_rejected(self):
        edits=[lambda d:d.update(schema='other'),lambda d:d.update(run_id='c'*32),lambda d:d.update(status='acquiring'),
               lambda d:d.update(manual_owner_hold=True),lambda d:d.update(recovery_hold=True),
               lambda d:d.update(driver_healthy=False),lambda d:d.update(quarantined_buffers=1),
               lambda d:d['cleanup'].update(motor_stop_confirmed=False),
               lambda d:d['cleanup']['camera'].update(no_active_capture_process=False),
               lambda d:d['final_diagnostics'].update(status=1),
               lambda d:d['final_diagnostics']['dma'].update(mm2s_sr=4096),
               lambda d:d['final_diagnostics']['dma'].update(s2mm_sr=4098|16)]
        for i,edit in enumerate(edits):
            with self.subTest(edit=i),tempfile.TemporaryDirectory() as tmp:
                run,d=self.fixture(Path(tmp));edit(d);(run/'trial.json').write_text(json.dumps(d))
                with self.assertRaises(RuntimeError):inspect_cleanup(run)
                self.assertTrue(run.exists())
    def test_tampered_camera_or_worker_rejected(self):
        for name in ['scan/camera_session/session_close.json','scan/persistent_camera_worker.py']:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                run,d=self.fixture(Path(tmp));(run/name).write_text('changed')
                with self.assertRaises(RuntimeError):inspect_cleanup(run)
    def test_child_and_camera_ownership_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            run,d=self.fixture(Path(tmp));c=CompatibleCancelMixin();c.process=object()
            with self.assertRaisesRegex(RuntimeError,'Child'):c._assert_discard_quiescent(run)
            c.process=None
            # Camera occupancy is mocked; no physical hardware is queried.
            with (patch('compatible_cancel.subprocess.run') as probe,
                  patch.dict('sys.modules',{'fcntl':MagicMock()}),patch('builtins.open',mock_open())):
                probe.return_value.returncode=0
                with self.assertRaisesRegex(RuntimeError,'Camera'):c._assert_discard_quiescent(run)
    def test_missing_cleanup_rejected_without_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)/('a'*32);run.mkdir()
            with self.assertRaises(RuntimeError):inspect_cleanup(run)
            self.assertTrue(run.exists())
if __name__=='__main__':unittest.main(verbosity=2)
