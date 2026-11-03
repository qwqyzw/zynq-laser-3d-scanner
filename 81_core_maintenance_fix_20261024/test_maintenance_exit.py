import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from test_dual_service import Checks

class Maintenance(unittest.TestCase):
 def test_idle_signal_preserves_completed_result_with_and_without_run_pointer(self):
  for attached in [True,False]:
   with tempfile.TemporaryDirectory() as t:
    root=Path(t);c=Checks().coordinator(root);rid='a'*32;run=root/'runs'/rid;run.mkdir(parents=True);(run/'measured.csv').write_text('retained')
    raw=json.dumps({'state':'ready','run_id':rid}).encode();(root/'latest_result.json').write_bytes(raw)
    c.run_id=rid if attached else None;c.run=run if attached else None;c.exiting=True
    with patch.object(c,'finish') as finish:c.request_stop()
    finish.assert_not_called();self.assertEqual((root/'latest_result.json').read_bytes(),raw);self.assertTrue((run/'measured.csv').exists());self.assertFalse(c.cancelled)
 def test_real_idle_btn1_still_discards(self):
  with tempfile.TemporaryDirectory() as t:
   c=Checks().coordinator(Path(t));c.run_id='a'*32;c.run=Path(t)/'runs'/c.run_id;c.run.mkdir(parents=True)
   with patch.object(c,'finish') as finish:c.request_stop()
   finish.assert_called_once_with('failed','User stop requested');self.assertTrue(c.cancelled)
if __name__=='__main__':unittest.main()
