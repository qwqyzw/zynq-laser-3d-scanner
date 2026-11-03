import json,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import visual_button_service as service

class Checks(unittest.TestCase):
    def controller(self,p):
        cfg=p/'config.json';cfg.write_text('{}')
        c=service.Coordinator({'config_file':cfg,'output_root':p,'mode':'diagnostic_preview','client_date':'2026-10-24'},
           {},startup_manifest_sha256='fixture66',compat_manifest_sha256='fixture68',visual_manifest_sha256='fixture71')
        c.run_id='a'*32;c.run=p/'runs'/c.run_id;(c.run/'model/viewer').mkdir(parents=True)
        (c.run/'model/viewer/export_manifest.json').write_text(json.dumps({'coordinate_producer':'zynq','test_only':False,'point_statistics':{'finite_points':100}}))
        (c.run/'model/viewer/source_meta.json').write_text(json.dumps({'diagnostic_preview_only':True,'alignment_validated':False,'metric_accuracy_accepted':False}))
        return c
    def test_model_child_only_after_reconstruction(self):
        with tempfile.TemporaryDirectory(prefix='SYNTHETIC_PIPELINE_') as tmp:
            c=self.controller(Path(tmp));c.stage='reconstructing_on_zynq'
            with patch.object(service,'verify'),patch.object(service.original.Coordinator,'launch') as launch:
                c.finish('ready_diagnostic_preview')
            _,args,stage=launch.call_args.args;self.assertEqual(stage,'surface_modelling_on_zynq')
            self.assertTrue(args[3].endswith('model_after_scan.py'));self.assertFalse((c.root/'latest_result.json').exists())
    def test_cpu_stop_does_not_kill_acquisition_owner(self):
        for stage in ['acquiring','surface_modelling_on_zynq']:
            with self.subTest(stage=stage),tempfile.TemporaryDirectory(prefix='SYNTHETIC_PIPELINE_') as tmp:
                c=self.controller(Path(tmp));c.stage=stage;c.process=MagicMock();c.process.pid=12345;c.process.poll.return_value=None
                (c.run/'scan').mkdir();c.request_stop()
                self.assertEqual(c.process.terminate.call_count,int(stage=='surface_modelling_on_zynq'))
                self.assertTrue(c.cancelled)
    def test_derived_manifest_published_atomically_with_measured_export(self):
        with tempfile.TemporaryDirectory(prefix='SYNTHETIC_PIPELINE_') as tmp:
            c=self.controller(Path(tmp));c.stage='surface_modelling_on_zynq';c.surface_returncode=0
            out=c.run/'model/visual_model';out.mkdir();(out/'artifact_manifest.json').write_text('{}')
            c.finish('ready_diagnostic_preview');record=json.loads((c.root/'latest_result.json').read_text())
            self.assertEqual(record['surface_kind'],'estimated_convex_surface');self.assertEqual(record['state'],'ready')
            self.assertFalse(record['metric_accuracy_accepted']);self.assertEqual(record['point_count'],100)
    def test_planar_or_failed_model_keeps_measured_export_with_reason(self):
        with tempfile.TemporaryDirectory(prefix='SYNTHETIC_PIPELINE_') as tmp:
            c=self.controller(Path(tmp));c.stage='surface_modelling_on_zynq';c.surface_returncode=1;c.finish('ready_diagnostic_preview')
            record=json.loads((c.root/'latest_result.json').read_text());self.assertTrue(record['surface_unavailable'])
            self.assertNotIn('surface_relative',record);self.assertEqual(record['point_count'],100)
if __name__=='__main__':unittest.main(verbosity=2)
