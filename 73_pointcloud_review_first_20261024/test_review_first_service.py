import tempfile,json,unittest
from pathlib import Path
from unittest.mock import patch
import review_first_service as service

class Checks(unittest.TestCase):
    def test_review_is_published_without_any_surface_child_or_manifest(self):
        with tempfile.TemporaryDirectory(prefix='SYNTHETIC_REVIEW_ONLY_') as tmp:
            root=Path(tmp);cfg=root/'config.json';cfg.write_text('{}')
            c=service.Coordinator({'config_file':cfg,'output_root':root,'mode':'diagnostic_preview','client_date':'2026-10-24'},
                {},review_manifest_sha256='fixture73',visual_manifest_sha256='fixture71',compat_manifest_sha256='fixture68',startup_manifest_sha256='fixture66')
            c.run_id='a'*32;c.run=root/'runs'/c.run_id;c.stage='reconstructing_on_zynq'
            viewer=c.run/'model/viewer';viewer.mkdir(parents=True)
            (viewer/'export_manifest.json').write_text(json.dumps({'coordinate_producer':'zynq','test_only':False,'point_statistics':{'finite_points':30}}))
            (viewer/'source_meta.json').write_text(json.dumps({'diagnostic_preview_only':True,'alignment_validated':False,'metric_accuracy_accepted':False}))
            review=c.run/'model/pointcloud_review';review.mkdir();(review/'review_manifest.json').write_text('{}')
            with patch.object(service,'verify'),patch.object(service,'review',return_value={'empty_unique_angles_deg':[30]}),patch.object(c,'launch') as launch:
                c.finish('ready_diagnostic_preview')
            launch.assert_not_called();record=json.loads((root/'latest_result.json').read_text())
            self.assertTrue(record['surface_review_required']);self.assertNotIn('surface_relative',record)
            self.assertEqual(record['state'],'ready');self.assertEqual(record['point_count'],30)
    def test_acquisition_and_owner_stop_paths_are_inherited(self):
        for name in ['poll','request_stop','attach','serve']:
            self.assertIs(getattr(service.Coordinator,name),getattr(service.base.Coordinator,name))
if __name__=='__main__':unittest.main(verbosity=2)
