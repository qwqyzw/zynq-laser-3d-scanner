import unittest,tempfile,json,sys
from pathlib import Path
from unittest.mock import patch
import cleanup86 as cleanup
P=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(P/'68_compatible_cancel_20261024'))
from test_compatible_cancel import Checks as OldFixtures

class Checks(unittest.TestCase):
 def fixture(self,root):
  run,d=OldFixtures().fixture(root);d.update(schema='paired_actual_PL_trial_v2',version='20261024.integer_tie_guard.v1',release_manifest_sha256='pin',managed_child_complete=True)
  names=['optimized_dual_scan.py','fuse_measured_profiles.py','diagnostic_camera.py','switchable_pl_camera.py','locked_switchable_camera_worker.py','temporal_consensus.py','roi_integer.py','foreground_select.py']
  snap=run/'tested_sources';snap.mkdir();files={}
  for n in names:(snap/n).write_text('# synthetic');files[n]=cleanup.sha(snap/n)
  d['source_sha256']=files;(run/'trial.json').write_text(json.dumps(d));return run,d,{'files':files}
 def test_final_safe_and_complete_receipt_accepted(self):
  for state in ['ready_diagnostic_preview','cancelled','failed']:
   with tempfile.TemporaryDirectory() as t:
    r,d,b=self.fixture(Path(t));d['status']=state;(r/'trial.json').write_text(json.dumps(d))
    with patch.object(cleanup,'verify_release',return_value=b):self.assertTrue(cleanup.inspect_cleanup(r,'pin')['record_validated'])
 def test_unsafe_owner_receipts_rejected(self):
  edits=[lambda d:d.update(managed_child_complete=False),lambda d:d.update(status='acquiring'),lambda d:d.update(release_manifest_sha256='bad'),lambda d:d.update(driver_healthy=False),lambda d:d.update(quarantined_buffers=1),lambda d:d['source_sha256'].pop('temporal_consensus.py'),lambda d:d['final_diagnostics']['dma'].update(s2mm_sr=4096)]
  for edit in edits:
   with tempfile.TemporaryDirectory() as t:
    r,d,b=self.fixture(Path(t));edit(d);(r/'trial.json').write_text(json.dumps(d))
    with patch.object(cleanup,'verify_release',return_value=b),self.assertRaises(RuntimeError):cleanup.inspect_cleanup(r,'pin')
    self.assertTrue(r.exists())
 def test_tampered_run_source_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   r,d,b=self.fixture(Path(t));(r/'tested_sources/temporal_consensus.py').write_text('changed')
   with patch.object(cleanup,'verify_release',return_value=b),self.assertRaises(RuntimeError):cleanup.inspect_cleanup(r,'pin')
if __name__=='__main__':unittest.main(verbosity=2)
