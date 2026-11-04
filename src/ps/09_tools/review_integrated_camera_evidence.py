"""Independently audit four recorded PL camera batches; never accesses hardware.

Usage: python 09_tools/review_integrated_camera_evidence.py EVIDENCE_DIR --output REVIEW.json
EVIDENCE_DIR contains completion.json and scan/{preflight,point_00..02}/images.
Actual accepted-column counts are recomputed from each batch, never prescribed.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import sys
import traceback

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '04_runtime'))
sys.path.insert(0, str(ROOT / '01_fpga_baseline/reference'))
import laser_horizontal_check
import laser_vertical_fast
import scan_stripe_quality
import quality_from_stripes
import quality_from_stripes_fast

TOLERANCE_PX = .25  # The frozen PS/PL acceptance contract; actual Q8 error is also reported.
BATCHES = ('preflight', 'point_00', 'point_01', 'point_02')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def require(value, message):
    if not value:
        raise ValueError(message)


def same_pixels(left, right, tolerance=0):
    require(left.shape == right.shape, 'Quality pixel array shape differs')
    require(np.array_equal(left[:, 0], right[:, 0]), 'Quality accepted columns differ')
    difference = float(np.max(abs(left[:, 1] - right[:, 1]), initial=0))
    require(difference <= tolerance, 'Quality median coordinates differ')
    return difference


def verify_reference_sources():
    """Bind the independent PS reference to the untouched 01 frozen manifest."""
    baseline = ROOT / '01_fpga_baseline'
    entries = {entry['path']: entry for entry in load_json(baseline / 'SHA256.json')['files']}
    report = {}
    for module in (laser_horizontal_check, laser_vertical_fast, scan_stripe_quality):
        name = 'reference/' + Path(module.__file__).name
        actual = digest(module.__file__)
        require(actual == entries[name]['sha256'], 'Imported reference differs from frozen source: ' + name)
        report[name] = {'sha256': actual, 'loaded_from': str(Path(module.__file__).resolve())}
    for module, frozen_name in (
            (quality_from_stripes, '03_host_optimization/source_snapshot/quality_from_stripes.py'),
            (quality_from_stripes_fast, '03_host_optimization/board_ready/quality_from_stripes_fast.py')):
        actual = digest(module.__file__)
        require(actual == digest(ROOT / frozen_name), 'Quality implementation differs from handoff: ' + frozen_name)
        report[frozen_name] = {'sha256': actual, 'loaded_from': str(Path(module.__file__).resolve())}
    return report


def review_batch(folder, roi, exposure=5):
    """Read and independently recompute one existing batch; does not edit it."""
    folder = Path(folder).resolve()
    require(len(roi) == 4 and all(type(v) is int for v in roi), 'ROI must contain four integers')
    require(0 <= roi[0] < roi[2] <= 640 and 0 <= roi[1] < roi[3] <= 480, 'Invalid camera ROI')
    record = load_json(folder / 'capture.json')
    require(record['status'] == 'completed', 'Capture did not complete')
    require(record['camera_release_completed'] is True, 'Camera release unconfirmed')
    require(record['ae_exposure_restoration_verified'] is True and record['restore_errors'] == [],
            'Exposure restoration unconfirmed')
    require(record['white_balance_gain_written'] is False, 'Unexpected WB/gain write')
    require(record['exposure_trial'] == exposure, 'Capture exposure differs from requested value')
    raw_names = [f'raw_{i:02d}.png' for i in range(3)]
    bright_names = [f'{phase}_{i:02d}.png' for phase in ('bright_before', 'bright_after') for i in range(3)]
    require(len(record['frames']) == 3 and [row['file'] for row in record['frames']] == raw_names,
            'Wrong raw frame count/order')
    require(len(record['bright_frames']) == 6 and {row['file'] for row in record['bright_frames']} == set(bright_names),
            'Wrong bright frame count or duplicate images')
    image_hashes = {}
    images = []
    original = record['original_controls']
    for row in record['frames'] + record['bright_frames']:
        image = folder / row['file']
        actual_hash = digest(image)
        require(actual_hash == row['sha256'], 'Original image hash mismatch: ' + row['file'])
        # imdecode supports Windows paths containing Chinese characters.
        pixels = cv2.imdecode(np.frombuffer(image.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        require(pixels is not None and pixels.shape == (480, 640, 3), 'Wrong image mode: ' + row['file'])
        image_hashes[row['file']] = actual_hash
        controls = row['controls_readback']
        if row['file'] in raw_names:
            require(controls['ae'] == 1 and controls['exp'] == exposure, 'Raw frame AE/exposure readback mismatch')
            images.append(pixels)
        else:
            require(controls['ae'] == original['ae'] and controls['exp'] == original['exp'],
                    'Bright image does not retain/restore original AE/exposure')

    transactions = load_json(folder / 'pl_transactions.json')
    require(len(transactions) == 3, 'Need three recorded PL transactions')
    references = []
    actual_stripes = []
    frames = []
    for index, image in enumerate(images):
        reference = laser_horizontal_check.extract_horizontal(image, roi, threshold=12, min_run=8)
        output = folder / f'pl_{index:02d}_actual.bin'
        raw = output.read_bytes()
        require(len(raw) == 640 * 4, 'PL output must contain exactly 640 words')
        words = np.frombuffer(raw, dtype='<u4')
        valid = (words & np.uint32(0x80000000)) != 0
        q8 = words & np.uint32(0x0003ffff)
        require(not np.any(words & np.uint32(0x7ffc0000)), 'PL reserved bits are nonzero')
        require(not np.any(words[~valid]), 'Invalid PL records must be zero')
        require(not np.any(q8[valid] >= 480 * 256), 'Valid PL coordinate outside image')
        y = np.where(valid, q8.astype(float) / 256, np.nan)
        mismatch = int(np.count_nonzero(reference['valid'] != valid))
        require(mismatch == 0, 'PS/PL valid bits differ')
        difference = float(np.max(abs(reference['y'][valid] - y[valid]), initial=0))
        require(difference <= TOLERANCE_PX, 'PS/PL Q8 coordinate difference exceeds frozen contract')
        entry = transactions[index]
        require(entry['frame'] == index and entry['raw_valid_columns'] == int(valid.sum()),
                'PL transaction frame/count differs from actual records')
        require(entry['source'] == 'supplied_PL_driver' and entry['PS_reference_computed'] is True,
                'PL transaction source/reference flags differ')
        require(entry['valid_equal'] is True and math.isclose(entry['max_pixel_difference'], difference, abs_tol=1e-12),
                'Recorded PS/PL comparison differs from independent comparison')
        require(type(entry['completed_frames']) is int and entry['completed_frames'] > 0,
                'PL completed-frame counter missing')
        require(type(entry['cycles']) is int and entry['cycles'] > 0, 'PL cycle counter invalid')
        references.append(reference)
        actual_stripes.append({'valid': valid, 'y': y})
        frames.append({'frame': index, 'raw_image_sha256': image_hashes[raw_names[index]],
                       'actual_PL_sha256': hashlib.sha256(raw).hexdigest(),
                       'raw_valid_columns': int(valid.sum()), 'valid_mismatches': mismatch,
                       'max_y_difference_px': difference, 'max_difference_Q8_units': difference * 256,
                       'within_half_Q8_LSB': difference <= 1 / 512,
                       'within_one_Q8_LSB': difference <= 1 / 256,
                       'completed_frames': entry['completed_frames'], 'cycles': entry['cycles']})

    # The original file-based gate uses cv2.imread; evidence paths should use
    # the canonical ASCII project root, as in the real runner.
    ps_original, q_original = scan_stripe_quality.common_stripe_pixels([folder / name for name in raw_names], roi)
    ps_slow, q_ps_slow = quality_from_stripes.review_from_stripes(images, references, roi)
    ps_fast, q_ps_fast = quality_from_stripes_fast.review_from_stripes_fast(images, references, roi)
    ps_fused, q_ps_fused = quality_from_stripes_fast.extract_and_review_fast(images, roi)
    pl_slow, q_pl_slow = quality_from_stripes.review_from_stripes(images, actual_stripes, roi)
    pl_fast, q_pl_fast = quality_from_stripes_fast.review_from_stripes_fast(images, actual_stripes, roi)
    reports = {'original_PS': q_original, 'slow_supplied_PS': q_ps_slow, 'fast_supplied_PS': q_ps_fast,
               'fused_PS': q_ps_fused, 'slow_supplied_PL': q_pl_slow, 'fast_supplied_PL': q_pl_fast}
    require(all(value == q_original for value in reports.values()), 'Slow/fast/PS/PL quality reports differ')
    for pixels in (ps_slow, ps_fast, ps_fused):
        same_pixels(ps_original, pixels)
    same_pixels(pl_slow, pl_fast)
    quality_difference = same_pixels(ps_original, pl_fast, TOLERANCE_PX)
    recorded_quality = load_json(folder / 'stripe_quality.json')
    require(recorded_quality == q_pl_fast, 'Saved quality report differs from recomputed PL quality')
    lines = (folder / 'stripe_pixels.csv').read_text(encoding='utf-8').splitlines()
    require(lines and lines[0] == 'x_px,y_px', 'Unexpected pixels CSV header')
    rows = [[float(value) for value in line.split(',')] for line in lines[1:] if line.strip()]
    require(all(len(row) == 2 for row in rows), 'Malformed pixels CSV')
    saved_pixels = np.asarray(rows, dtype=float).reshape(-1, 2)
    same_pixels(saved_pixels, pl_fast)
    comparison = load_json(folder / 'quality_comparison.json')
    require(comparison['valid_columns_equal'] is True and comparison['quality_reports_equal'] is True,
            'Recorded quality comparison did not pass')
    require(math.isclose(comparison['max_y_difference_px'], quality_difference, abs_tol=1e-12),
            'Recorded quality median difference is inconsistent')
    require(q_original['image_quality_gate_passed'], 'Original image quality gate failed')
    return {'status': 'independent_batch_review_passed', 'folder': str(folder),
            'capture_json_sha256': digest(folder / 'capture.json'), 'exposure': exposure,
            'nine_original_hashes_and_modes_verified': True,
            'raw_manual_exposure_readbacks_verified': True, 'bright_AE_exposure_restoration_verified': True,
            'camera_release_confirmed': True, 'original_image_sha256': image_hashes,
            'frames': frames, 'quality_reports': reports,
            'common_quality_columns': q_original['common_quality_columns'],
            'quality_columns_per_frame': q_original['quality_columns_per_frame'],
            'csv_rows': len(saved_pixels), 'saved_quality_and_csv_verified': True,
            'quality_max_y_difference_px': quality_difference}


def review_evidence(root):
    root = Path(root).resolve()
    result = {'status': 'failed', 'offline_only': True, 'hardware_accessed': False,
              'evidence_root': str(root), 'python': sys.version,
              'numpy': np.__version__, 'opencv': cv2.__version__,
              'comparison_tolerance_px': TOLERANCE_PX, 'batches': [], 'errors': []}
    try:
        result['reference_sources'] = verify_reference_sources()
        completion = load_json(root / 'completion.json')
        session = load_json(root / 'scan/session.json')
        roi = tuple(completion['roi'])
        exposure = completion['exposure']
        require(exposure == 5, 'This integrated acceptance run requires the selected exposure5')
        result.update(client_date=completion['client_date'], roi=list(roi), exposure=exposure,
                      completion_json_sha256=digest(root / 'completion.json'),
                      scan_session_sha256=digest(root / 'scan/session.json'))
        for batch in BATCHES:
            try:
                report = review_batch(root / 'scan' / batch / 'images', roi, exposure)
                report['batch'] = batch
                result['batches'].append(report)
            except Exception as exc:
                result['batches'].append({'batch': batch, 'status': 'failed', 'error': str(exc)})
                result['errors'].append(batch + ': ' + str(exc))
        require(len(result['batches']) == 4 and not result['errors'], 'One or more camera batches failed')
        require(completion['status'] == 'integrated_static_and_small_scan_passed', 'Top-level run did not pass')
        require(completion['same_overlay_throughout'] is True
                and completion['overlay_before'] == completion['overlay_after'], 'Overlay identity changed')
        require(session['status'] == 'completed' and len(session['points']) == 3, 'Scan did not complete all three poses')
        require(session['requested_angles_deg'] == [0, 5, 0], 'Unexpected requested scan angles')
        require(session['cleanup']['motor_stop_confirmed'] is True
                and session['cleanup']['motor_client_closed'] is True, 'Motor cleanup not confirmed')
        require(session['cleanup']['camera']['camera_release_confirmed'] is True
                and session['cleanup']['camera']['exposure_restoration_verified'] is True,
                'Session camera cleanup not confirmed')
        static = completion['static_cases']
        require(len(static) == 56 and all(row['passed'] for row in static), 'Frozen static prefix did not pass56 cases')
        static_counters = [row['completed_frames'] for row in static]
        require(static_counters == list(range(1, 57)), 'Static PL counters are not sequential1..56')
        frame_counters = [frame['completed_frames'] for batch in result['batches'] for frame in batch['frames']]
        require(frame_counters == list(range(57, 69)), 'Camera PL counters are not continuous after56 static transactions')
        bindings = [session['preflight_capture']] + [point['capture'] for point in session['points']]
        for batch, binding in zip(result['batches'], bindings):
            require(binding['exposure'] == exposure and binding['source'] == 'hardware_camera',
                    'Session capture source/exposure differs')
            require(binding['stripe_coordinate_source'] == 'supplied_PL_driver',
                    'Session coordinates are not recorded as PL outputs')
            require(binding['stripe_quality'] == batch['quality_reports']['fast_supplied_PL'],
                    'Session quality differs from independent batch audit')
            require(len(binding['images']) == 3, 'Session image count differs')
            for index, image in enumerate(binding['images']):
                require(image['sha256'] == batch['frames'][index]['raw_image_sha256'],
                        'Session original-image hash differs from capture record')
        require(completion['driver_healthy'] is True and completion['quarantined_buffers'] == 0,
                'Driver ownership/health not clean at completion')
        result.update(status='independent_integrated_camera_review_passed',
                      batches_reviewed=4, original_images_verified=36, actual_PL_outputs_reviewed=12,
                      continuous_PL_completed_counters=frame_counters,
                      common_quality_columns=[batch['common_quality_columns'] for batch in result['batches']],
                      geometry_accuracy_accepted=False)
    except Exception as exc:
        result['errors'].append(str(exc))
        result['traceback'] = traceback.format_exc()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence_root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = review_evidence(args.evidence_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({name: report.get(name) for name in
                      ('status', 'original_images_verified', 'actual_PL_outputs_reviewed',
                       'common_quality_columns', 'errors')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'independent_integrated_camera_review_passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
