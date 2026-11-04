"""Validate a completed object scan; compute/export its XYZ only on a Zynq.

The default performs file/schema/hash checks, without pixel rays or real XYZ.
Separately versioned fast candidate: actual PL quality is rechecked without
rerunning the six-implementation regression audit. There is no PC XYZ bypass.
Imported mathematical helpers may be exercised with labelled synthetic tests.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import shutil
import sys
import time
import traceback
from types import SimpleNamespace

DEFAULT_PROJECT = Path(__file__).resolve().parents[1]
MIN_OBJECT_HEIGHT_MM = 3.0
MAX_OBJECT_RADIUS_MM = 60.0

CURRENT_RELEASE = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_RELEASE))
from fast_release import VERSION as FAST_VERSION, REQUIRED_FILES as FAST_FILES, verify_release
from single_pl_review import review_batch as production_review_batch


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value, label):
    require(type(value) in (int, float) and math.isfinite(value), label + ' must be finite')
    return float(value)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(part)
    return value.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False) + '\n', encoding='utf-8')


def child(root, name):
    """No supplied relative name may escape an evidence/source root."""
    require(isinstance(name, str) and name and '\\' not in name, 'Invalid relative evidence path')
    value = (root / name).resolve()
    require(not Path(name).is_absolute() and value.is_relative_to(root.resolve()),
            'Evidence path escapes its root')
    return value


def modules(project):
    project = Path(project).resolve()
    # Source identity is checked below; never load a different project's module.
    for directory in ('01_fpga_baseline/reference', '04_runtime', '09_tools'):
        sys.path.insert(0, str(project / directory))
    names = ('pl_runtime', 'laser_stripe', 'geometric_calibration', 'geometry_core',
             'turntable_coordinates', 'quality_from_stripes_fast',
             'review_integrated_camera_evidence', 'object_board_scan',
             'board_pointcloud_export')
    result = {}
    for name in names:
        module = importlib.import_module(name)
        require(Path(module.__file__).resolve().is_relative_to(project),
                'Imported code is outside selected project: ' + name)
        result[name] = module
    quality = importlib.import_module('quality_columns10')
    require(Path(quality.__file__).resolve() == CURRENT_RELEASE / 'quality_columns10.py', 'Wrong quality10 module')
    result['quality_from_stripes_fast'] = quality
    # The independent review has its own project-derived frozen-source checks.
    require(result['review_integrated_camera_evidence'].ROOT.resolve() == project,
            'Camera reviewer belongs to another project')
    import object_board_scan_360
    require(Path(object_board_scan_360.__file__).resolve() == CURRENT_RELEASE / 'object_board_scan_360.py',
            'Full-turn acquisition module belongs to another release')
    result['object_board_scan'] = object_board_scan_360
    return SimpleNamespace(**result)


def previous_calibration_evidence(model):
    from geometry_evidence import validate_geometry_evidence
    return validate_geometry_evidence(model)

def diagnostic_geometry_evidence(model):
    return previous_calibration_evidence(model)

def validate_geometry(model, span, mods, *, diagnostic_preview=False):
    """Schema/math invariants only: no observed pixels or XYZ are evaluated."""
    import numpy as np
    require(0 <= span <= 360, 'Full-turn route must remain within0..360 degrees')
    diagnostic = diagnostic_geometry_evidence(model) if diagnostic_preview else {}
    if not diagnostic_preview:
        require(model.get('shape_preview_candidate_usable') is True,
                'Current geometry is not usable even as a shape preview')
    require(model.get('object_used_in_geometry_fit') is False,
            'Object must not have been used to fit its own geometry')
    require(diagnostic_preview and model.get('historical_extrinsics_used') is False
            and model.get('axis_revalidation_skipped_by_user') is False
            and model.get('full_axis_validation_passed') is True,
            'Current independently checked target-axis preview geometry required')
    require(model.get('metric_accuracy_accepted') is False,
            'This pipeline only supports explicitly unaccepted metric accuracy')
    camera, axis = model['camera'], model['axis']
    require(camera['image_size'] == [640, 480], 'Geometry camera mode must be 640x480')
    mods.geometric_calibration.camera_arrays(camera)
    for key in ('laser_plane', 'reference_plane'):
        plane = model[key]
        mods.geometry_core.unit_plane(plane['normal'], finite(plane['offset_mm'], key + ' offset'))
    if not diagnostic_preview:
        require(axis.get('candidate_checks_passed') is True, 'Axis candidate checks failed')
    zero = finite(axis['motor_zero_reference_deg'], 'Geometry motor zero')
    if span > 10 and not diagnostic_preview:
        require(axis.get('large_angle_axis_validation_passed') is True,
                'A scan spanning more than 10 degrees needs large-angle axis validation')
    direction = np.asarray(axis['axis_direction_camera'], dtype=float)
    centre = np.asarray(axis['axis_point_on_initial_paper_mm'], dtype=float)
    basis = np.asarray(axis['turntable_basis_camera_columns'], dtype=float)
    require(direction.shape == centre.shape == (3,) and basis.shape == (3, 3)
            and all(np.isfinite(item).all() for item in (direction, centre, basis)),
            'Invalid axis arrays')
    require(np.isclose(np.linalg.norm(direction), 1, atol=1e-6)
            and np.allclose(basis.T @ basis, np.eye(3), atol=1e-6)
            and np.isclose(np.linalg.det(basis), 1, atol=1e-6)
            and np.allclose(basis[:, 2], direction, atol=1e-6),
            'Axis and right-handed orthonormal table basis disagree')
    return {'motor_zero_reference_deg': zero, 'scan_span_deg': span,
            'large_angle_axis_validation_passed': axis.get('large_angle_axis_validation_passed', False),
            'shape_preview_candidate_usable': model['shape_preview_candidate_usable'],
            'metric_accuracy_accepted': False, **diagnostic}


def validate_scan(scan, mods):
    require(scan.get('status') == 'completed'
            and scan.get('real_hardware_acquisition_verified') is True
            and scan.get('hardware_accessed') is True and scan.get('simulation') is False,
            'Only a completed verified real hardware acquisition can be reconstructed')
    require(scan.get('acquisition_completed') is True and scan.get('forward_full_turn_completed') is True
            and scan.get('return_to_origin_completed') is False
            and scan.get('normal_return_strategy') == 'complete_forward_full_turn_no_reverse'
            and scan.get('unwrapped_feedback_rezeroed') is False,
            'Acquisition and normal finite return must both complete')
    require(scan.get('same_overlay_throughout') is True
            and scan['overlay_before'] == scan['overlay_after'], 'Scan overlay identity changed')
    require(scan.get('driver_healthy') is True and scan.get('quarantined_buffers') == 0,
            'Acquisition driver was not clean at completion')
    cleanup = scan['cleanup']
    camera = cleanup['camera']
    require(cleanup.get('motor_stop_confirmed') is True
            and cleanup.get('motor_client_closed') is True
            and camera.get('camera_release_confirmed') is True
            and camera.get('exposure_restoration_verified') is True
            and camera.get('no_active_capture_process') is True,
            'Final motor/camera cleanup was not verified')
    plan = scan['plan']
    expected = mods.object_board_scan.nominal_plan(plan['max_angle_deg'])
    for key in ('acquisition_angles_deg', 'return_angles_deg', 'expected_capture_batches',
                'expected_new_PL_transactions', 'expected_original_images'):
        require(plan[key] == expected[key], 'Scan plan differs from finite route: ' + key)
    points = scan['points']
    require(len(points) == len(expected['acquisition_angles_deg']), 'Incomplete scan points')
    origin = finite(scan['global_origin_motor_deg'], 'Global scan origin')
    require(finite(scan['motor_origin_deg'], 'Motor origin alias') == origin, 'Origin aliases differ')
    angles = []
    for index, point in enumerate(points):
        require(point['index'] == index and point['status'] == 'completed', 'Point order/status differs')
        require(point['global_angle_deg'] == expected['acquisition_angles_deg'][index],
                'Requested point angle differs from scan plan')
        require(point['global_origin_motor_deg'] == origin
                and point['target_motor_deg'] == origin + point['global_angle_deg'], 'Point origin/target changed')
        before = finite(point['motor_position_before_capture_deg'], 'Absolute capture angle')
        after = finite(point['motor_position_after_capture_deg'], 'Absolute post-capture angle')
        require(abs(before - point['target_motor_deg']) <= .3 and abs(after - before) <= .1,
                'Arrival or capture drift exceeds original gates')
        require(math.isclose(point['capture_drift_deg'], after - before, abs_tol=1e-12)
                and math.isclose(point['feedback_relative_deg'], before - origin, abs_tol=1e-12),
                'Feedback/drift fields contradict absolute feedback')
        state = point['motor_status_after_capture']
        require(state.get('position_reached') is True and state.get('stall_detected') is False
                and state.get('stall_protection_triggered') is False,
                'Motor did not remain stopped without fault')
        angles.append(before)
    returning = scan['return_points']
    require([row['global_angle_deg'] for row in returning] == expected['return_angles_deg']
            and all(row['status'] == 'completed' for row in returning), 'Finite return is incomplete')
    end = scan['return_origin_capture']
    require(abs(finite(end['motor_position_before_capture_deg'], 'Return position') - origin - 360) <= .3
            and abs(finite(end['motor_position_after_capture_deg'], 'Return final position') - origin - 360) <= .4
            and abs(end['capture_drift_deg']) <= .1, 'Returned origin or recapture drift differs')
    require(scan['completed_capture_batches'] == expected['expected_capture_batches'], 'Batch count differs')
    baseline = scan['baseline_completed_frames']
    require(type(baseline) is int and 0 <= baseline <= 0xffffffff, 'Invalid starting PL counter')
    final = (baseline + expected['expected_new_PL_transactions']) & 0xffffffff
    require(scan['last_verified_completed_frames'] == final, 'Final PL counter differs')
    mods.object_board_scan.assert_idle(scan['diagnostics'], completed=final, require_completed=True)
    return max(angles) - min(angles)


def verify_sources(project, root, scan, mods):
    """Require snapshot, recorded and current validated source bytes to agree."""
    hashes = load(root / 'source_fingerprints.json')
    require(hashes and hashes == scan['source_hashes'], 'Recorded scan source fingerprints differ')
    needed = {str(path.relative_to(project).as_posix()) for path in (project / '04_runtime').glob('*.py')}
    needed.update(('04_runtime/rig_config.json', '14_integrated_release_20261020/SHA256.json',
                   CURRENT_RELEASE.relative_to(project).as_posix() + '/object_board_scan_360.py',
                   CURRENT_RELEASE.relative_to(project).as_posix() + '/check_object_scan_plan_360.py',
                   '09_tools/run_integrated_board.py'))
    needed.update((CURRENT_RELEASE.relative_to(project) / name).as_posix() for name in (*FAST_FILES, 'SHA256.json'))
    require(needed.issubset(hashes), 'Scan did not snapshot every required source')
    for name, expected in hashes.items():
        require(digest(child(root / 'tested_sources', name)) == expected,
                'Scan source snapshot changed: ' + name)
        require(digest(child(project, name)) == expected, 'Current source differs from scan: ' + name)
    artifacts = mods.pl_runtime.inspect_integrated_artifacts(project)
    if 'button_release' in scan:
        from button_overlay_runtime import inspect_button_release
        selected = scan['button_release']
        release = child(project, selected['relative_directory'] + '/SHA256.json').parent
        button = inspect_button_release(project, release)
        require(button['manifest_sha256'] == selected['manifest_sha256'],
                'Button release manifest differs from scan binding')
        extra = set(button['source_sha256']) | {selected['relative_directory'] + '/SHA256.json'}
        require(extra.issubset(hashes), 'Scan omitted button release/source snapshots')
        artifacts['button_release'] = button
        bit = button['laser_motor_button.bit']
    else:
        bit = artifacts['integrated_release']['laser_motor.bit']
    identity = scan['overlay_before']
    require(identity['sha256'] == bit['sha256'], 'Acquisition used another bitstream')
    require(math.isclose(finite(identity['fclk0_mhz'], 'Acquisition clock'), 50, abs_tol=.01),
            'Acquisition clock was not 50 MHz')
    reference = mods.review_integrated_camera_evidence.verify_reference_sources()
    return {'scan_sources': hashes, 'artifacts': artifacts, 'quality_reference_sources': reference}


def capture_groups(scan):
    yield 'preflight', scan['preflight_capture']
    for point in scan['points']:
        yield 'point_%03d' % point['index'], point['capture']
    yield 'return_origin', scan['return_origin_capture']['capture']


def verify_batch_files(root, label, batch, roi, exposure, counter):
    """Hash/schema/packed-word checks, without rays or image-derived XYZ."""
    import numpy as np
    folder = child(root, label + '/images')
    require(batch.get('source') == 'hardware_camera'
            and batch.get('stripe_coordinate_source') == 'supplied_PL_driver'
            and batch.get('PS_reference_only') is True, 'Capture has no actual PL provenance')
    require(batch['exposure'] == exposure and batch.get('camera_release_confirmed') is False
            and batch.get('exposure_restored') is False
            and batch.get('session_still_streaming') is True, 'Capture flags/exposure differ')
    # Resolve supplied paths exactly on the original board; local copied evidence
    # uses canonical session-relative files for the default read-only check.
    require(Path(batch['stripe_pixels_file']).name == 'stripe_pixels.csv'
            and Path(batch['capture_record']).name == 'capture.json', 'Wrong capture output names')
    record = load(folder / 'capture.json')
    require(record['status'] == 'completed' and record.get('camera_release_completed') is False
            and record.get('ae_exposure_restoration_verified') is False
            and record.get('schema') == 'persistent_manual_exposure_v1'
            and record.get('session_still_streaming') is True and record['restore_errors'] == [],
            'Capture AE/exposure restoration failed')
    require(record['exposure_trial'] == exposure and record['white_balance_gain_written'] is False,
            'Unexpected capture exposure/WB setting')
    raw_names = ['raw_%02d.png' % i for i in range(3)]
    bright_names = []
    require([row['file'] for row in record['frames']] == raw_names
            and sorted(row['file'] for row in record['bright_frames']) == sorted(bright_names),
            'Need three persistent-session original camera frames')
    require(len(batch['images']) == 3, 'Capture binding needs three raw originals')
    files = {}
    for row in record['frames'] + record['bright_frames']:
        file = child(folder, row['file'])
        require(digest(file) == row['sha256'], 'Original image SHA256 differs: ' + label + '/' + row['file'])
        controls = row['controls_readback']
        original = record['original_controls']
        require((controls['ae'], controls['exp']) ==
                ((1, exposure) if row['file'] in raw_names else (original['ae'], original['exp'])),
                'Image AE/exposure readback differs')
        files[file.relative_to(root).as_posix()] = row['sha256']
    for index, row in enumerate(batch['images']):
        require(Path(row['file']).name == raw_names[index]
                and row['sha256'] == record['frames'][index]['sha256'], 'Raw capture binding differs')
    rows = load(folder / 'pl_transactions.json')
    require(len(rows) == 3, 'Each batch requires three PL transactions')
    for index, row in enumerate(rows):
        require(row['frame'] == index
                and row['completed_frames'] == ((counter + index + 1) & 0xffffffff),
                'PL counters are not continuous across batches')
        require(row['source'] == 'supplied_PL_driver' and row['PS_reference_computed'] is True
                and row['valid_equal'] is True and 0 <= finite(row['max_pixel_difference'], 'PL error') <= .25
                and type(row['cycles']) is int and row['cycles'] > 0, 'PL comparison or timing flags differ')
        raw = (folder / ('pl_%02d_actual.bin' % index)).read_bytes()
        require(len(raw) == 2560, 'PL output must contain 640 words')
        words = np.frombuffer(raw, '<u4')
        valid = words >> 31 != 0
        require(not np.any(words & np.uint32(0x7ffc0000)) and not np.any(words[~valid])
                and not np.any((words[valid] & 0x3ffff) >= 480 * 256), 'Invalid PL record packing')
        require(row['raw_valid_columns'] == int(valid.sum()), 'PL valid count differs from actual binary')
    quality = load(folder / 'stripe_quality.json')
    comparison = load(folder / 'quality_comparison.json')
    require(batch['stripe_quality'] == quality and quality['image_quality_gate_passed'] is True,
            'Saved/bound image quality differs')
    require(comparison['valid_columns_equal'] is True and comparison['quality_reports_equal'] is True
            and 0 <= finite(comparison['max_y_difference_px'], 'Quality pixel error') <= .25,
            'Quality comparison failed')
    for name in ['capture.json', 'pl_transactions.json', 'stripe_quality.json',
                 'quality_comparison.json', 'stripe_pixels.csv'] + ['pl_%02d_actual.bin' % i for i in range(3)]:
        file = folder / name
        files[file.relative_to(root).as_posix()] = digest(file)
    return (counter + 3) & 0xffffffff, files


def inspect(project, root, geometry, mods, *, diagnostic_preview=False):
    scan, model = load(root / 'completion.json'), load(geometry)
    span = validate_scan(scan, mods)
    geometry_review = validate_geometry(model, scan['plan']['max_angle_deg'], mods,
                                        diagnostic_preview=diagnostic_preview)
    geometry_review['feedback_scan_span_deg'] = span
    source_review = verify_sources(project, root, scan, mods)
    roi = scan['roi']
    require(len(roi) == 4 and all(type(value) is int for value in roi)
            and 0 <= roi[0] < roi[2] <= 640 and 0 <= roi[1] < roi[3] <= 480, 'Invalid ROI')
    require(type(scan['exposure']) is int and scan['exposure'] > 0, 'Invalid fixed exposure')
    inputs = {name: digest(root / name) for name in
              ('completion.json', 'source_fingerprints.json', 'verified_artifacts.json', 'nominal_plan.json')}
    inputs.update({'tested_sources/' + name: value
                   for name, value in source_review['scan_sources'].items()})
    require(load(root / 'nominal_plan.json') == scan['plan'], 'Saved nominal plan differs')
    from persistent_evidence import verify_session
    inputs.update(verify_session(root, scan, digest, load, require))
    counter = scan['baseline_completed_frames']
    for label, batch in capture_groups(scan):
        counter, hashes = verify_batch_files(root, label, batch, roi, scan['exposure'], counter)
        inputs.update(hashes)
        if label.startswith('point_'):
            pointfile = root / label / 'point.json'
            require(load(pointfile) == scan['points'][int(label[6:])], 'Saved point checkpoint differs')
            inputs[pointfile.relative_to(root).as_posix()] = digest(pointfile)
    require(counter == scan['last_verified_completed_frames'], 'Batch PL total differs')
    return scan, model, {'status': 'files_and_schema_verified_no_XYZ_computed',
                        'coordinate_computation_performed': False, 'hardware_accessed': False,
                        'geometry': geometry_review, 'source_review': source_review,
                        'input_sha256': inputs, 'geometry_sha256': digest(geometry),
                        'original_image_count': 3 * (len(scan['points']) + 2),
                        'session_bright_original_count': 6,
                        'new_PL_transactions': 3 * (len(scan['points']) + 2)}


def actual_pl_pixels(folder, roi, mods):
    """Only actual PL words feed XYZ. PS is a separate cross-check in review_batch."""
    import cv2
    import numpy as np
    images, stripes = [], []
    for index in range(3):
        images.append(cv2.imdecode(np.frombuffer((folder / ('raw_%02d.png' % index)).read_bytes(),
                                                np.uint8), cv2.IMREAD_COLOR))
        words = np.frombuffer((folder / ('pl_%02d_actual.bin' % index)).read_bytes(), '<u4')
        valid = words >> 31 != 0
        stripes.append({'valid': valid, 'y': np.where(valid, (words & 0x3ffff) / 256., np.nan)})
    pixels, quality = mods.quality_from_stripes_fast.review_from_stripes_fast(images, stripes, roi)
    require(quality == load(folder / 'stripe_quality.json') and quality['image_quality_gate_passed'],
            'Recomputed actual PL quality differs')
    saved = np.loadtxt(folder / 'stripe_pixels.csv', delimiter=',', skiprows=1).reshape(-1, 2)
    require(np.array_equal(pixels, saved), 'CSV differs from recomputed actual PL coordinates')
    return pixels


def reconstruct_profile(pixels, absolute_motor_deg, model, mods, *, diagnostic_preview=False):
    """Pure math for measured pixels; tests may call with explicitly synthetic inputs."""
    import numpy as np
    angle = finite(absolute_motor_deg, 'Absolute angle') - finite(
        model['axis']['motor_zero_reference_deg'], 'Geometry motor zero')
    rays = mods.geometric_calibration.pixel_rays(pixels, model['camera'])
    xyz, height, valid = mods.geometry_core.reconstruct(
        rays, model['laser_plane'], model['reference_plane'])
    local = np.full_like(xyz, np.nan)
    radius = np.full(len(xyz), np.nan)
    if np.any(valid):
        if diagnostic_preview:
            # Do not forge candidate_checks_passed to call the strict helper.
            # The same rigid math is applied while preserving every failed gate.
            import cv2
            validate_geometry(model, 0, mods, diagnostic_preview=True)
            axis = model['axis']
            rotation = cv2.Rodrigues(np.asarray(axis['axis_direction_camera']) * np.radians(angle))[0]
            local[valid] = ((xyz[valid] - np.asarray(axis['axis_point_on_initial_paper_mm'])) @ rotation) @ \
                np.asarray(axis['turntable_basis_camera_columns'])
        else:
            local[valid] = mods.turntable_coordinates.to_initial_table_frame(xyz[valid], angle, model['axis'])
        direction = np.asarray(model['axis']['axis_direction_camera'], float)
        delta = xyz[valid] - np.asarray(model['axis']['axis_point_on_initial_paper_mm'], float)
        perpendicular = delta - (delta @ direction)[:, None] * direction
        radius[valid] = np.linalg.norm(perpendicular, axis=1)
    selected = valid & (height >= MIN_OBJECT_HEIGHT_MM) & (radius <= MAX_OBJECT_RADIUS_MM)
    reasons = []
    for i in range(len(xyz)):
        reason = []
        if not valid[i]:
            reason.append('invalid_ray_plane_intersection')
        else:
            if height[i] < MIN_OBJECT_HEIGHT_MM:
                reason.append('below_3mm_reference_height')
            if radius[i] > MAX_OBJECT_RADIUS_MM:
                reason.append('outside_60mm_axis_radius')
        reasons.append(';'.join(reason) if reason else 'included_object_preview')
    return {'angle_deg': angle, 'camera_xyz': xyz, 'table_xyz': local, 'height': height,
            'radius': radius, 'valid': valid, 'selected': selected, 'reasons': reasons}


def unchanged(root, geometry, inspection):
    require(digest(geometry) == inspection['geometry_sha256'], 'Geometry changed during reconstruction')
    for name, expected in inspection['input_sha256'].items():
        require(digest(child(root, name)) == expected, 'Scan input changed during reconstruction: ' + name)


def reconstruct_board(project, root, geometry, output, mods, *, diagnostic_preview=False,
                      fast_manifest_sha256=None):
    # This gate is before inspect/math/output creation, with no allow-dev escape.
    identity = mods.board_pointcloud_export.board_identity()
    descriptions = ' '.join(str(identity.get(key) or '') for key in
                            ('device_tree_model', 'device_tree_compatible')).lower()
    require(identity['is_linux_arm_zynq_or_pynq'] and 'zynq' in descriptions,
            'Real XYZ computation requires Linux ARM and an actual Zynq device tree')
    fast_binding = verify_release(project, CURRENT_RELEASE, fast_manifest_sha256)
    require(diagnostic_preview is True, 'This candidate only supports explicit diagnostic previews')
    require(digest(geometry) == fast_binding['profile']['geometry_sha256'],
            'Fast candidate diagnostic geometry changed')
    require(not output.exists(), 'Choose a new output directory')
    scan, model, inspection = inspect(project, root, geometry, mods,
                                      diagnostic_preview=diagnostic_preview)
    require(model.get('execution_role') == 'zynq',
            'Actual reconstruction requires a geometry model computed on Zynq')
    require(not mods.board_pointcloud_export.contains_marker(scan)
            and not mods.board_pointcloud_export.contains_marker(model),
            'Development/test/synthetic evidence cannot become a real object model')
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {'status': 'checking', 'data_origin': 'hardware_scan', 'coordinate_producer': 'zynq',
              'execution_platform': identity, 'client_date': scan['client_date'],
              'device_wall_time_unverified': datetime.now(timezone.utc).isoformat(),
              'scan_directory': str(root), 'geometry_file': str(geometry),
              'metric_accuracy_accepted': False, 'surface_fitted': False,
              'diagnostic_preview_only': diagnostic_preview,
              'alignment_validated': not diagnostic_preview,
              'synthetic': False, 'synthetic_points_added': 0, 'hardware_accessed': False,
              'input_audit': inspection, 'batch_reviews': [], 'profiles': [],
              'fast_software_release': fast_binding,
              'current_geometry_revalidated': False,
              'old_current_view_evidence_is_current_run_validation': False,
              'historical_extrinsics_reused': True, 'geometry_reuse_authorized': True,
              'quality_review_mode': 'single_actual_PL_quality_recheck',
              'acquisition_PS_crosschecks_preserved': True,
              'cross_implementation_regression_audit_performed': False}
    try:
        save(output / 'reconstruction.json', report)
        shutil.copyfile(geometry, output / 'geometry_used.json')
        save(output / 'axis_used.json', model['axis'])
        report['axis_snapshot_sha256'] = digest(output / 'axis_used.json')
        source_files = [
            Path(__file__), project / '09_tools/board_pointcloud_export.py',
            project / '09_tools/render_object_preview.py',
            project / '09_tools/review_integrated_camera_evidence.py']
        source_files.extend(CURRENT_RELEASE / name for name in FAST_FILES
                            if name != Path(__file__).name)
        source_files.append(CURRENT_RELEASE / 'SHA256.json')
        source_files.extend((project / '04_runtime').glob('*.py'))
        source_files.extend(project / '01_fpga_baseline' / name
                            for name in source_review_names(inspection))
        report['reconstruction_source_sha256'] = {}
        for source in source_files:
            name = source.resolve().relative_to(project).as_posix()
            target = output / 'tested_sources' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            report['reconstruction_source_sha256'][name] = digest(target)
            require(digest(source) == report['reconstruction_source_sha256'][name],
                    'Reconstruction source changed during snapshot')
        require(digest(output / 'geometry_used.json') == inspection['geometry_sha256'],
                'Geometry changed during evidence copy')
        # Recompute all images, including preflight and return. Neither supplies
        # object vertices; only the requested acquisition poses do.
        for label, batch in capture_groups(scan):
            folder = root / label / 'images'
            for item in batch['images']:
                require(Path(item['file']).resolve().is_relative_to(root)
                        and Path(item['file']).resolve().parent == folder,
                        'Board capture points outside its current session')
            review = production_review_batch(folder, tuple(scan['roi']), scan['exposure'])
            report['batch_reviews'].append({'label': label, **review})
            print('RECON_PL_CHECK', label, review['common_quality_columns'], flush=True)
            save(output / 'reconstruction.json', report)
        unchanged(root, geometry, inspection)
        fields = ['point_index', 'pixel_index', 'x_px', 'y_px', 'motor_absolute_deg',
                  'geometry_relative_deg', 'camera_x_mm', 'camera_y_mm', 'camera_z_mm',
                  'x_mm', 'y_mm', 'z_mm', 'height_above_reference_mm', 'axis_radius_mm',
                  'valid_intersection', 'included_object_preview', 'selection_reason']
        count, valid_count, object_count = 0, 0, 0
        report['status'] = 'computing_on_zynq'
        with (output / 'scene_all.csv').open('x', newline='', encoding='utf-8') as scene, \
                (output / 'object_points.csv').open('x', newline='', encoding='utf-8') as obj:
            scene_writer, object_writer = csv.DictWriter(scene, fields), csv.DictWriter(obj, fields)
            scene_writer.writeheader()
            object_writer.writeheader()
            for point in scan['points']:
                pixels = actual_pl_pixels(root / ('point_%03d' % point['index']) / 'images',
                                          tuple(scan['roi']), mods)
                absolute = point['motor_position_before_capture_deg']
                result = reconstruct_profile(pixels, absolute, model, mods,
                                             diagnostic_preview=diagnostic_preview)
                for i, pixel in enumerate(pixels):
                    valid, selected = bool(result['valid'][i]), bool(result['selected'][i])
                    numbers = list(pixel) + [absolute, result['angle_deg']] + list(result['camera_xyz'][i]) + \
                        list(result['table_xyz'][i]) + [result['height'][i], result['radius'][i]]
                    # Empty invalid values stay in the scene audit, never the PLY.
                    numbers = [float(v) if math.isfinite(float(v)) else '' for v in numbers]
                    row = dict(zip(fields, [point['index'], i] + numbers +
                                   [valid, selected, result['reasons'][i]]))
                    scene_writer.writerow(row)
                    if selected:
                        object_writer.writerow(row)
                    count += 1
                    valid_count += valid
                    object_count += selected
                report['profiles'].append({'point_index': point['index'], 'absolute_motor_deg': absolute,
                    'angle_from_geometry_zero_deg': result['angle_deg'], 'PL_quality_pixels': len(pixels),
                    'valid_intersections': int(result['valid'].sum()),
                    'object_preview_points': int(result['selected'].sum())})
                print('RECON_PROFILE', point['index'], len(pixels), int(result['selected'].sum()), flush=True)
        report.update(total_PL_quality_points=count, valid_scene_points=valid_count,
                      object_preview_points=object_count,
                      excluded_points=count - object_count,
                      selection={'reference_height_min_mm': MIN_OBJECT_HEIGHT_MM,
                                 'axis_radius_max_mm': MAX_OBJECT_RADIUS_MM,
                                 'coordinate_adjustment_applied': False})
        require(object_count > 0, 'No measured points pass explicit object/background separation')
        unchanged(root, geometry, inspection)
        from fullturn_closure import check_closure
        closure = check_closure(output / 'object_points.csv', scan)
        save(output / 'fullturn_closure.json', closure)
        report['fullturn_closure'] = closure
        require(closure['passed'], 'Full-turn endpoint optical closure failed; preserve original measured points without export')
        metadata = {'title': '本轮物体 · Zynq 生成的三维实测点云', 'units': 'mm',
                    'axis_units': {axis: 'mm' for axis in 'xyz'}, 'scale_status': 'nominal',
                    'metric_accuracy_accepted': False, 'data_origin': 'hardware_scan',
                    'diagnostic_preview_only': bool(diagnostic_preview),
                    'alignment_validated': not diagnostic_preview,
                    'coordinate_producer': 'zynq', 'synthetic': False,
                    'geometry_status': '名义尺度三维点云；毫米精度未验收',
                    'coverage_note': '仅显示本轮实测可见表面；未测区域不补点、不补面。背景按高度≥3 mm、距轴≤60 mm分离，scene_all.csv保留全部测点及排除理由。',
                    'scan_session': str(root), 'client_date': scan['client_date'],
                    'geometry_sha256': inspection['geometry_sha256'],
                    'axis_snapshot_sha256': report['axis_snapshot_sha256'],
                    'completion_sha256': inspection['input_sha256']['completion.json'],
                    'original_image_count': inspection['original_image_count'],
                    'object_acquisition_pose_count': len(scan['points']),
                    'new_PL_transactions_verified': inspection['new_PL_transactions'],
                    'nominal_scan_span_deg': scan['plan']['max_angle_deg'],
                    'feedback_scan_span_deg': inspection['geometry']['feedback_scan_span_deg'],
                    'motor_angle_rule': 'absolute motor feedback minus geometry motor_zero_reference_deg',
                    'all_ray_PS_crosscheck_and_axis_compensation_executed_on': 'zynq',
                    'execution_platform': identity,
                    'source_images': [{'path': str(root / 'camera_session/bright_after_session/bright_01.png'),
                                       'label': '本轮完成整圈后的原始亮图'}]}
        if diagnostic_preview:
            metadata.update(diagnostic_preview_metadata(model))
        metadata.update(fast_software_version=FAST_VERSION,
                        fast_manifest_sha256=fast_binding['manifest_sha256'],
                        current_geometry_revalidated=False,
                        old_current_view_evidence_is_current_run_validation=False,
                        historical_extrinsics_reused=True, geometry_reuse_authorized=True)
        metadata.update(fullturn_closure=closure, angular_acquisition_complete=True,
                        full_axis_validation_passed=False, axis_extrapolation_beyond_measured60deg=True)
        save(output / 'object_metadata.json', metadata)
        report['export'] = mods.board_pointcloud_export.export(
            output / 'object_points.csv', output / 'object_metadata.json', output / 'viewer')
        unchanged(root, geometry, inspection)
        for name, expected in report['reconstruction_source_sha256'].items():
            require(digest(project / name) == expected, 'Reconstruction source changed during execution')
        verify_release(project, CURRENT_RELEASE, fast_manifest_sha256)
        report.update(status=('board_diagnostic_preview_exported' if diagnostic_preview
                              else 'board_reconstruction_and_export_completed'),
                      coordinate_computation_performed=True, PS_used_only_as_crosscheck=True,
                      coordinate_source='recomputed_quality_from_actual_PL_words',
                      real_model_generated_on_zynq=not diagnostic_preview,
                      diagnostic_preview_generated_on_zynq=diagnostic_preview,
                      full_object_coverage_verified=False)
        if diagnostic_preview:
            report['diagnostic_geometry_evidence'] = diagnostic_geometry_evidence(model)
    except BaseException as exc:
        report.update(status='failed', error=type(exc).__name__ + ': ' + str(exc),
                      traceback=traceback.format_exc(), real_model_generated_on_zynq=False)
        raise
    finally:
        report['elapsed_s'] = time.monotonic() - started
        save(output / 'reconstruction.json', report)
    return report


def diagnostic_preview_metadata(model):
    evidence = diagnostic_geometry_evidence(model)
    return {'title': '360°扫描 · 当前转轴与激光标定点云',
            'geometry_status': '当前棋盘转轴和激光高度检查已通过粗预览门限；完整外形与毫米精度未验收',
            'coverage_note': '当前转轴使用0至360度棋盘姿态检查。整圈采集不保证所有表面可见；未测区域不补点、不补面。背景仍按高度≥3mm、轴距≤60mm筛选。',
            'metric_accuracy_accepted': False, **evidence}


def source_review_names(inspection):
    return [name for name in inspection['source_review']['quality_reference_sources']
            if name.startswith('reference/')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=DEFAULT_PROJECT)
    parser.add_argument('--fast-manifest-sha256', required=True,
                        help='Pin this separate software candidate manifest')
    parser.add_argument('--scan', required=True, type=Path)
    parser.add_argument('--geometry', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--run-board', action='store_true')
    parser.add_argument('--diagnostic-preview', action='store_true',
                        help='Explicit unaccepted failed-axis experiment; all current laser/input checks remain required')
    args = parser.parse_args()
    project, root, geometry = args.project.resolve(), args.scan.resolve(), args.geometry.resolve()
    try:
        binding = verify_release(project, CURRENT_RELEASE, args.fast_manifest_sha256)
        require(args.diagnostic_preview, 'Select explicit diagnostic-preview mode for this candidate')
        require(digest(geometry) == binding['profile']['geometry_sha256'],
                'Fast candidate diagnostic geometry differs')
        mods = modules(project)
        if args.run_board:
            require(args.output is not None, '--output is required for board reconstruction')
            report = reconstruct_board(project, root, geometry, args.output.resolve(), mods,
                                       diagnostic_preview=args.diagnostic_preview,
                                       fast_manifest_sha256=args.fast_manifest_sha256)
            result = {key: report[key] for key in ('status', 'object_preview_points',
                       'total_PL_quality_points', 'real_model_generated_on_zynq')}
            result['output'] = str(args.output.resolve())
            result['fast_software_release'] = binding
        else:
            _, _, report = inspect(project, root, geometry, mods,
                                   diagnostic_preview=args.diagnostic_preview)
            result = {key: report[key] for key in ('status', 'coordinate_computation_performed',
                       'hardware_accessed', 'geometry', 'original_image_count', 'new_PL_transactions')}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except Exception as exc:
        parser.exit(2, 'Reconstruction refused: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
