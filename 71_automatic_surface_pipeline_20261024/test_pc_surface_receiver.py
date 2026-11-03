import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pc_visual_receiver as m

class Checks(unittest.TestCase):
    def fixture(self,root,historical=False):
        viewer=root/'viewer';viewer.mkdir();(viewer/'points.csv').write_bytes(b'SYNTHETIC RECEIVER FIXTURE')
        sid='b'*32;rid='a'*32;prefix='/scanner/runs/'+rid+'/model/visual_model'
        meta={'coordinate_producer':'zynq','historical_algorithm_preview':historical,'source_run_id':rid,
              'source_sha256':hashlib.sha256((viewer/'points.csv').read_bytes()).hexdigest()}
        files={'model_metadata.json':json.dumps(meta).encode(),'ESTIMATED_CONVEX_SURFACE.ply':b'SYNTHETIC RECEIVER MESH FIXTURE',
               'model_viewer.html':b'<p>synthetic receiver fixture</p>'}
        manifest={'coordinate_producer':'zynq','derived_estimate':True,'historical_algorithm_preview':historical,
             'files':{k:{'sha256':hashlib.sha256(v).hexdigest(),'bytes':len(v)} for k,v in files.items()}}
        raw=json.dumps(manifest).encode();remote={prefix+'/'+k:v for k,v in files.items()};remote[prefix+'/artifact_manifest.json']=raw
        record={'run_id':rid,'surface_relative':'runs/'+rid+'/model/visual_model','surface_manifest_sha256':hashlib.sha256(raw).hexdigest()}
        return viewer,record,remote
    def test_receive_only_verified_current_zynq_mesh(self):
        with tempfile.TemporaryDirectory(prefix='SYNTHETIC_RECEIVER_') as tmp:
            viewer,record,remote=self.fixture(Path(tmp))
            with patch.object(m,'old_download',return_value=(viewer,{})),patch.object(m.base,'read_remote',side_effect=lambda s,p,n:remote[p]):
                out,receipt=m.download(None,'/scanner',record,Path(tmp))
            self.assertTrue(receipt['surface_received']);self.assertTrue((out.parent/'visual_model/ESTIMATED_CONVEX_SURFACE.ply').exists())
    def test_historical_preview_never_published_as_current(self):
        with tempfile.TemporaryDirectory(prefix='SYNTHETIC_RECEIVER_') as tmp:
            viewer,record,remote=self.fixture(Path(tmp),historical=True)
            with patch.object(m,'old_download',return_value=(viewer,{})),patch.object(m.base,'read_remote',side_effect=lambda s,p,n:remote[p]):
                with self.assertRaisesRegex(ValueError,'Live Zynq'):m.download(None,'/scanner',record,Path(tmp))
    def test_wrong_path_or_corrupt_manifest_rejected(self):
        for mutate in [lambda r:r.update(surface_relative='../escape'),lambda r:r.update(surface_manifest_sha256='0'*64)]:
            with tempfile.TemporaryDirectory(prefix='SYNTHETIC_RECEIVER_') as tmp:
                viewer,record,remote=self.fixture(Path(tmp));mutate(record)
                with patch.object(m,'old_download',return_value=(viewer,{})),patch.object(m.base,'read_remote',side_effect=lambda s,p,n:remote[p]):
                    with self.assertRaises(ValueError):m.download(None,'/scanner',record,Path(tmp))
if __name__=='__main__':unittest.main(verbosity=2)
