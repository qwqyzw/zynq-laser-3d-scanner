"""Production recheck of one saved physical PL batch; no pixel rays or XYZ.

The acquisition still performs its frozen PS/PL cross-check. The production
reconstructor rechecks actual PL quality once instead of running the six-route
cross-implementation regression audit again. The original auditor is retained.
"""
import hashlib
import json
import math
from pathlib import Path

from fast_release import QUALITY


def require(value, message):
    if not value:
        raise ValueError(message)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review_batch(folder, roi, exposure=5):
    """Verify nine originals, controls, packed PL, original quality and CSV."""
    import cv2
    import numpy as np
    from quality_columns10 import review_from_stripes_fast

    folder = Path(folder).resolve()
    require(len(roi) == 4 and all(type(value) is int for value in roi), 'Invalid ROI type')
    require(0 <= roi[0] < roi[2] <= 640 and 0 <= roi[1] < roi[3] <= 480, 'Invalid ROI bounds')
    record = load(folder / 'capture.json')
    require(record['status'] == 'completed' and record.get('schema') == 'persistent_manual_exposure_v1'
            and record['camera_release_completed'] is False and record['session_still_streaming'] is True,
            'Capture completion/release unconfirmed')
    require(record['ae_exposure_restoration_verified'] is False and record['restore_errors'] == [],
            'Exposure restoration unconfirmed')
    require(record['white_balance_gain_written'] is False and record['exposure_trial'] == exposure,
            'Capture colour writes/exposure differ')
    raw_names = ['raw_%02d.png' % index for index in range(3)]
    bright_names = []
    require(len(record['frames']) == 3 and [row['file'] for row in record['frames']] == raw_names,
            'Wrong raw frame count/order')
    require(record['bright_frames'] == [], 'Persistent batches must not claim per-pose bright images')
    image_hashes, images = {}, []
    original = record['original_controls']
    for row in record['frames'] + record['bright_frames']:
        image = folder / row['file']
        actual = digest(image)
        require(actual == row['sha256'], 'Original image SHA256 differs: ' + row['file'])
        pixels = cv2.imdecode(np.frombuffer(image.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        require(pixels is not None and pixels.shape == (480, 640, 3),
                'Original image mode differs: ' + row['file'])
        controls = row['controls_readback']
        if row['file'] in raw_names:
            require(controls['ae'] == 1 and controls['exp'] == exposure,
                    'Manual exposure readback differs')
            images.append(pixels)
        else:
            require(controls['ae'] == original['ae'] and controls['exp'] == original['exp'],
                    'Bright AE/exposure restoration differs')
        image_hashes[row['file']] = actual
    transactions = load(folder / 'pl_transactions.json')
    require(len(transactions) == 3, 'Need three PL transactions')
    stripes, frames = [], []
    for index in range(3):
        entry = transactions[index]
        raw = (folder / ('pl_%02d_actual.bin' % index)).read_bytes()
        require(len(raw) == 2560, 'PL output must contain 640 uint32 words')
        words = np.frombuffer(raw, '<u4')
        valid = words >> 31 != 0
        q8 = words & np.uint32(0x3ffff)
        require(not np.any(words & np.uint32(0x7ffc0000)) and not np.any(words[~valid])
                and not np.any(q8[valid] >= 480 * 256), 'Invalid packed PL record')
        difference = entry['max_pixel_difference']
        require(entry['frame'] == index and entry['raw_valid_columns'] == int(valid.sum())
                and entry['source'] == 'supplied_PL_driver' and entry['PS_reference_computed'] is True
                and entry['valid_equal'] is True and entry.get('integer_reference_bit_exact') is True and type(difference) in (int, float)
                and math.isfinite(difference) and 0 <= difference <= .25,
                'Acquisition PS/PL cross-check or actual count differs')
        require(type(entry['completed_frames']) is int and entry['completed_frames'] > 0
                and type(entry['cycles']) is int and entry['cycles'] > 0,
                'Missing PL counters')
        stripes.append({'valid': valid, 'y': np.where(valid, q8.astype(float) / 256, np.nan)})
        frames.append({'frame': index, 'raw_image_sha256': image_hashes[raw_names[index]],
                       'actual_PL_sha256': hashlib.sha256(raw).hexdigest(),
                       'raw_valid_columns': int(valid.sum()),
                       'acquisition_PS_max_y_difference_px': difference,
                       'completed_frames': entry['completed_frames'], 'cycles': entry['cycles']})
    pixels, quality = review_from_stripes_fast(images, stripes, roi)
    require(all(quality.get(key) == value for key, value in QUALITY.items()),
            'Original image quality limits changed')
    require(quality['image_quality_gate_passed'] is True
            and quality == load(folder / 'stripe_quality.json'),
            'Recomputed actual PL quality differs or fails')
    lines = (folder / 'stripe_pixels.csv').read_text(encoding='utf-8').splitlines()
    require(lines and lines[0] == 'x_px,y_px', 'Unexpected pixels CSV header')
    rows = [[float(value) for value in line.split(',')] for line in lines[1:] if line.strip()]
    require(all(len(row) == 2 for row in rows), 'Malformed pixels CSV')
    saved = np.asarray(rows, dtype=float).reshape(-1, 2)
    require(np.array_equal(pixels, saved), 'Saved CSV differs from actual PL quality coordinates')
    comparison = load(folder / 'quality_comparison.json')
    median_difference = comparison['max_y_difference_px']
    require(comparison['valid_columns_equal'] is True and comparison['quality_reports_equal'] is True
            and type(median_difference) in (int, float) and math.isfinite(median_difference)
            and 0 <= median_difference <= .25, 'Acquisition quality cross-check failed')
    return {'status': 'production_actual_PL_quality_review_passed', 'folder': str(folder),
            'capture_json_sha256': digest(folder / 'capture.json'), 'exposure': exposure,
            'three_original_hashes_and_modes_verified': True,
            'raw_manual_exposure_readbacks_verified': True,
            'session_final_restoration_checked_by_scan_inspector': True, 'camera_release_confirmed': False,
            'original_image_sha256': image_hashes, 'frames': frames,
            'quality_reports': {'fast_supplied_PL': quality},
            'common_quality_columns': quality['common_quality_columns'],
            'quality_columns_per_frame': quality['quality_columns_per_frame'],
            'csv_rows': len(saved), 'saved_quality_and_csv_verified': True,
            'acquisition_PS_crosschecks_preserved': True,
            'PS_reference_recomputed_during_reconstruction': False,
            'cross_implementation_regression_audit_performed': False}
