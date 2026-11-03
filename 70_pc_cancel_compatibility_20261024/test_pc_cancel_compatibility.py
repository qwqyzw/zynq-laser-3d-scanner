from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from pc_cancel_receiver import compatible_discard

class Checks(unittest.TestCase):
    def test_restart_session_removes_same_run_only_and_closes_only_its_viewers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rid='a'*32;old='b'*32+'_'+rid;new='c'*32+'_'+rid;other='d'*32+'_'+'e'*32
            for key in [old,other]:(root/key).mkdir()
            receipts={old:{'cloudcompare_pid':123},other:{'cloudcompare_pid':456}}
            with patch('pc_cancel_receiver.pc_discard.close_owned_viewer',return_value=True) as close:
                result=compatible_discard(new,receipts,root)
            self.assertFalse((root/old).exists());self.assertTrue((root/other).exists())
            self.assertIn(other,receipts);self.assertEqual(result['owned_viewers_closed'],1)
            self.assertEqual(close.call_count,1)
            self.assertNotIn({'cloudcompare_pid':456},[c.args[0] for c in close.call_args_list])
    def test_idempotent_without_saved_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity='a'*32+'_'+'b'*32
            with patch('pc_cancel_receiver.pc_discard.close_owned_viewer',return_value=False):
                compatible_discard(identity,{},Path(tmp));compatible_discard(identity,{},Path(tmp))
    def test_invalid_scope_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for identity in ['../bad','a_b','a'*32+'_'+'../bad']:
                with self.assertRaises(ValueError):compatible_discard(identity,{},Path(tmp))
if __name__=='__main__':unittest.main(verbosity=2)
