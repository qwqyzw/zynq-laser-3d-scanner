"""Offline camera configuration and archived-image regressions; no devices opened."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
import zipfile

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '04_runtime'))
from scan_fixed_exposure import PerPoseExposureCamera
from scan_checked_exposure import StripeCheckedExposureCamera
from pl_scan_camera import PLStripeCheckedCamera
from host_image_fast import pack_bgr_fast
from laser_stripe import StripeResult


SYNTHETIC_WORKER = '''
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1]);exposure=int(sys.argv[2])
frames=[];bright=[]
for phase in ('raw','bright_before','bright_after'):
    for i in range(3):
        name='%s_%02d.png'%(phase,i)
        content=('synthetic-test-only:'+name).encode()
        (root/name).write_bytes(content)
        row={'file':name,'sha256':hashlib.sha256(content).hexdigest(),
             'controls_readback':{'ae':1,'exp':exposure}}
        (frames if phase=='raw' else bright).append(row)
record={'status':'completed','camera_release_completed':True,
        'ae_exposure_restoration_verified':True,'restore_errors':[],
        'exposure_trial':exposure,'frames':frames,'bright_frames':bright}
{mutation}
(root/'capture.json').write_text(json.dumps(record))
'''


def synthetic_worker(mutation=''):
    # Literal replacement preserves Python dictionary braces in the fixture.
    return SYNTHETIC_WORKER.replace('{mutation}', mutation)


class ExposureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='camera_exp_offline_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_explicit_settings_and_legacy_defaults(self):
        self.assertEqual(PerPoseExposureCamera('').exposure, 20)
        self.assertEqual(StripeCheckedExposureCamera('', (0, 0, 1, 1)).exposure, 20)
        self.assertEqual(PLStripeCheckedCamera('', (175, 340, 400, 400), object()).exposure, 5)
        for exposure in (5, 20):
            self.assertEqual(PerPoseExposureCamera('', exposure=exposure).exposure, exposure)
            self.assertEqual(StripeCheckedExposureCamera('', (0, 0, 1, 1), exposure=exposure).exposure, exposure)
            self.assertEqual(PLStripeCheckedCamera('', (0, 0, 1, 1), object(), exposure=exposure).exposure, exposure)

    def test_invalid_exposure_rejected_before_worker_or_hardware(self):
        for value in (0, -5, True, False, 5.0, '5', None, 0x80000000):
            with self.subTest(exposure=value), self.assertRaises(ValueError):
                PerPoseExposureCamera('', exposure=value)

    def test_invalid_deadline_rejected_before_worker_or_hardware(self):
        for value in (0, -1, 41, float('inf'), float('nan'), True, '40', None):
            with self.subTest(deadline=value), self.assertRaises(ValueError):
                PerPoseExposureCamera('', exposure=5, timeout_s=value)

    def test_requested_exposure_reaches_child_and_is_verified(self):
        for exposure in (5, 20):
            with self.subTest(exposure=exposure):
                folder = self.root / str(exposure)
                folder.mkdir()
                camera = PerPoseExposureCamera(synthetic_worker(), exposure=exposure, timeout_s=10)
                prepared = camera.start(folder)
                batch = camera.capture(folder / 'capture')
                self.assertEqual(prepared['exposure'], exposure)
                self.assertEqual(batch['exposure'], exposure)
                record = json.loads(Path(batch['capture_record']).read_text())
                self.assertEqual(record['exposure_trial'], exposure)
                self.assertEqual([r['controls_readback']['exp'] for r in record['frames']], [exposure] * 3)
                self.assertTrue(camera.close()['camera_release_confirmed'])

    def assert_failed_capture_cannot_retry(self, mutation, message):
        camera = PerPoseExposureCamera(synthetic_worker(mutation), exposure=5, timeout_s=10)
        camera.start(self.root)
        with self.assertRaisesRegex(RuntimeError, message):
            camera.capture(self.root / 'first')
        with self.assertRaisesRegex(RuntimeError, 'not ready'):
            camera.capture(self.root / 'second')
        self.assertFalse((self.root / 'second').exists())
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
            camera.close()

    def test_wrong_reported_exposure_stops_session(self):
        self.assert_failed_capture_cannot_retry("record['exposure_trial']=20", 'record exposure')

    def test_wrong_frame_readback_stops_session(self):
        self.assert_failed_capture_cannot_retry("record['frames'][1]['controls_readback']['exp']=20", 'readback')

    def test_auto_exposure_readback_stops_session(self):
        self.assert_failed_capture_cannot_retry("record['frames'][1]['controls_readback']['ae']=3", 'readback')

    def test_tampered_original_image_stops_session(self):
        self.assert_failed_capture_cannot_retry("(root/'raw_00.png').write_bytes(b'tampered')", 'hash mismatch')

    def test_unconfirmed_restoration_stops_session(self):
        self.assert_failed_capture_cannot_retry("record['ae_exposure_restoration_verified']=False", 'restoration')

    def test_mislabeled_bright_frame_cannot_count_as_laser_frame(self):
        self.assert_failed_capture_cannot_retry(
            "record['frames'][0],record['bright_frames'][0]=record['bright_frames'][0],record['frames'][0]",
            'fixed-exposure frames')


class ArchivedPLTests(unittest.TestCase):
    """Replay old measurements through the formal adapter, never pretend new PL work."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='camera_pl_replay_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        archive = ROOT / '13_system_test_20261020/原始测试证据.zip'
        with zipfile.ZipFile(archive) as bundle:
            for group, marker in (('exp5', 'exposure5_diagnostic/'),
                                  ('exp20', 'live_PL_camera/images/')):
                target = self.root / group
                target.mkdir()
                for entry in bundle.namelist():
                    if marker not in entry:
                        continue
                    name = entry.split(marker, 1)[1]
                    if '/' not in name and (name.endswith('.png') or name.endswith('.bin') or name == 'capture.json'):
                        (target / name).write_bytes(bundle.read(entry))
                self.assertTrue((target / 'capture.json').exists())

    def run_archived_capture(self, group, exposure):
        source = self.root / group
        record = PerPoseExposureCamera._verified_record(source, expected_exposure=exposure)
        originals = [cv2.imread(str(source / f'raw_{i:02d}.png')) for i in range(3)]
        archived_words = []
        for i in range(3):
            name = ('PL_' if group == 'exp5' else 'pl_') + f'{i:02d}_actual.bin'
            archived_words.append(np.fromfile(source / name, dtype='<u4'))
        test = self

        class ArchiveDriver:
            healthy = True
            count = 0

            def run_words(self, words, config):
                index = self.count
                self.count += 1
                np.testing.assert_array_equal(words, pack_bgr_fast(originals[index]))
                test.assertEqual((config.x0, config.y0, config.x1, config.y1), (175, 340, 400, 400))
                test.assertEqual(config.threshold_q8, 3072)
                test.assertEqual(config.min_run, 8)
                output = archived_words[index].copy()
                valid = (output >> 31).astype(bool)
                y = np.where(valid, (output & 0x3ffff).astype(float) / 256, np.nan)
                return StripeResult(output, valid, y, 0, self.count)

        # A test-only child copies immutable evidence and checks the actual CLI value.
        worker = (
            'from pathlib import Path\nimport shutil,sys\n'
            f'source=Path({str(source)!r})\n'
            f'assert int(sys.argv[2])=={exposure}\n'
            'target=Path(sys.argv[1])\n'
            "for item in source.iterdir():\n"
            "    if item.suffix=='.png' or item.name=='capture.json':\n"
            "        shutil.copyfile(item,target/item.name)\n"
        )
        session = self.root / ('session_' + group)
        session.mkdir()
        driver = ArchiveDriver()
        camera = PLStripeCheckedCamera(worker, (175, 340, 400, 400), driver,
                                       exposure=exposure, timeout_s=10, verify_ps=True)
        camera.start(session)
        folder = session / 'capture'
        if group == 'exp5':
            batch = camera.capture(folder)
            test.assertEqual(batch['stripe_quality']['common_quality_columns'], 129)
            test.assertTrue(batch['stripe_quality']['image_quality_gate_passed'])
            test.assertFalse(camera.capture_blocked)
        else:
            with test.assertRaisesRegex(RuntimeError, 'Quality failed'):
                camera.capture(folder)
            quality = json.loads((folder / 'stripe_quality.json').read_text())
            test.assertEqual(quality['common_quality_columns'], 11)
            test.assertFalse(quality['image_quality_gate_passed'])
            test.assertTrue(camera.capture_blocked)
            with test.assertRaisesRegex(RuntimeError, 'not ready'):
                camera.capture(session / 'must_not_retry')
        test.assertEqual(driver.count, 3)
        transactions = json.loads((folder / 'pl_transactions.json').read_text())
        test.assertTrue(all(t['valid_equal'] and t['max_pixel_difference'] <= 1 / 512 for t in transactions))
        test.assertEqual(record['exposure_trial'], exposure)
        test.assertTrue(camera.close()['camera_release_confirmed'])

    def test_exp5_real_images_and_archived_PL_outputs_pass_unchanged_quality(self):
        self.run_archived_capture('exp5', 5)

    def test_exp20_real_images_still_fail_without_automatic_retry_or_relaxed_quality(self):
        self.run_archived_capture('exp20', 20)


def main():
    suite = unittest.TestSuite()
    for case in (ExposureTests, ArchivedPLTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'offline_only': True, 'current_hardware_accessed': False,
                      'archived_measurements_only': True, 'tests_run': result.testsRun,
                      'all_passed': result.wasSuccessful()}, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
