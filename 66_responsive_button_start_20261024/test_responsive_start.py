"""Synthetic button/launch regression; no physical camera, GPIO or XYZ."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
import responsive_button_service as new


class Checks(unittest.TestCase):
    def config(self,root):
        f=root/'config.json';f.write_text('{}')
        cfg={'output_root':root/'out','config_file':f,'project':new.PROJECT,
            'overlay_release':new.PROJECT/'17_button_scan_release_20261020',
            'fast_release':new.BASE,'client_date':'2026-10-24','mode':'diagnostic_preview',
            'roi':[175,210,440,340],'exposure':5,'max_angle':360}
        art={'button_release':{'manifest_sha256':'base17'},
             'fast_release':{'manifest_sha256':new.BASE_PIN,'profile_sha256':'profile61'}}
        return cfg,art

    def test_ack_before_one_second_preserves_hold(self):
        events=[];g=new.AcknowledgingGate(new.legacy.original_service.ButtonGate(),lambda *v:events.append(v))
        for bits,now in [(0,0),(0,.1),(1,.2),(1,.3)]:self.assertIsNone(g.update(bits,now,False))
        self.assertEqual(events,[('button_pressed',.3)])
        self.assertIsNone(g.update(1,.8,False))
        self.assertEqual(g.update(1,1.31,False),'start')
        self.assertIsNone(g.update(1,3,False))

    def test_short_press_releases_without_start(self):
        events=[];g=new.AcknowledgingGate(new.legacy.original_service.ButtonGate(),lambda *v:events.append(v))
        for bits,now in [(0,0),(0,.1),(1,.2),(1,.3),(0,.4),(0,.5)]:
            self.assertIsNone(g.update(bits,now,False))
        self.assertEqual([v[0] for v in events],['button_pressed','released_before_start'])

    def test_busy_stop_unchanged_and_no_queued_start(self):
        g=new.AcknowledgingGate(new.legacy.original_service.ButtonGate(),lambda *v:None)
        for b,t in [(0,0),(0,.1),(1,.2),(1,.3)]:g.update(b,t,True)
        self.assertIsNone(g.update(1,2,False))
        g.update(2,3,True);self.assertEqual(g.update(2,3.1,True),'stop')
        self.assertIsNone(g.update(2,4,True))

    def test_one_full_parent_inspection_exact_legacy_child_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,art=self.config(Path(tmp));c=new.Coordinator(cfg,art,startup_manifest_sha256='synthetic')
            proc=MagicMock();proc.pid=12345
            with (patch.object(new,'own_identity'),patch.object(new.legacy,'inspect_config',return_value=(cfg,art)) as verify,
                 patch.object(new.legacy.original_service.subprocess,'Popen',return_value=proc) as popen):
                c.start()
            self.assertEqual(verify.call_count,1)
            args=popen.call_args.args[0]
            self.assertEqual(args[3],str(new.BASE/'object_board_scan_360.py'))
            self.assertEqual(args[args.index('--exposure')+1],'5')
            self.assertEqual(args[args.index('--max-angle')+1],'360')
            self.assertEqual(args[args.index('--fast-manifest-sha256')+1],new.BASE_PIN)
            self.assertIsNone(c._launch_verification)
            timing=json.loads((c.run/'startup_timing.json').read_text());self.assertEqual(timing['full_parent_inspections'],1)
            c.log.close()

    def test_failed_verification_launches_nothing_and_cache_not_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,art=self.config(Path(tmp));c=new.Coordinator(cfg,art,startup_manifest_sha256='synthetic')
            with (patch.object(new,'own_identity'),patch.object(new.legacy,'inspect_config',side_effect=ValueError('changed')),
                 patch.object(new.legacy.original_service.subprocess,'Popen') as launch):
                with self.assertRaises(ValueError):c.start()
            launch.assert_not_called();self.assertIsNone(c._launch_verification)

    def test_btn1_during_verification_prevents_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,art=self.config(Path(tmp));c=new.Coordinator(cfg,art,startup_manifest_sha256='synthetic')
            c.gpio=MagicMock();c.gpio.read.return_value=2
            with (patch.object(new,'own_identity'),patch.object(new.legacy,'inspect_config',return_value=(cfg,art)),
                 patch.object(new.legacy.original_service.subprocess,'Popen') as launch):c.start()
            launch.assert_not_called();self.assertIsNone(c.run_id);self.assertEqual(c.state,'ready')

    def test_no_permanent_verification_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,art=self.config(Path(tmp));c=new.Coordinator(cfg,art,startup_manifest_sha256='synthetic')
            with patch.object(new.legacy,'inspect_config',return_value=(cfg,art)) as verify:
                c.verified_current();c.verified_current()
            self.assertEqual(verify.call_count,2)
            for name in ['poll','serve','attach','request_stop','finish']:
                self.assertIs(getattr(new.Coordinator,name),getattr(new.legacy.Coordinator,name))


if __name__=='__main__':unittest.main(verbosity=2)
