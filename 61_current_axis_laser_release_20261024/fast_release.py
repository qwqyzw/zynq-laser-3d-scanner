"""Read-only identity/profile checks for the separately versioned software candidate."""
import hashlib
import json
from pathlib import Path
import re

VERSION = '20261024.current_axis.current_laser.preview.v12'
BASE_MANIFEST = '4a5b4d9689a507a7a0a771d85623d98656a830deb177a14f0e519f1f784a852c'
BASE_BIT = '3bc36e8d0a90bf2880d6943d0c34cecefcbbffa70d6f71e5365b998677b780aa'
GEOMETRY = 'a6581863f0e89d6bd89d3f70f7787a6f6c1b8c2f891e20e1a494d93a08267f14'
BASE_SOURCES = {
    '01_fpga_baseline/reference/integer_model.py': '649dce68848873bde66785d1149b8d84334343b8b9580d1e4e9f47846b739fdb',
    '09_tools/reconstruct_object_on_zynq.py': 'd829fd9882cfe2a0d969ec0f33793cc4871d014d5130fc635b40aa836f8a7dad',
    '09_tools/board_button_scan_service.py': 'cadee7265f89592a0d2c456ca192832108b6e39d377be5e9b7762789bf55d98f',
    '09_tools/load_button_overlay.py': '53914d8a50f4b571b4ce98e5327addd8ba467ceb47c17447eb12b2ca7da6732b',
    '09_tools/review_integrated_camera_evidence.py': 'ee1bca5350e6f302208a4bb7274bdbc3a5029ca2cbbda6ef0f0d420de566c9e7',
    '04_runtime/quality_from_stripes_fast.py': 'e029abbc65825ec1dfa345d9a7df4780ffa3645a722dfb7a0f73eaabaed0e805',
}
REQUIRED_FILES = (
    'axis_candidate.json', 'axis_acceptance_report.json', 'laser_training_report.json', 'geometry_evidence.py', 'laser_candidate.json', 'laser_holdout_report.json', 'axis_source_geometry.json', 'fast_release.py', 'single_pl_review.py', 'fast_reconstruct_on_zynq.py',
    'quality_columns10.py', 'test_quality10.py', 'cancel_discard.py', 'test_cancel_discard.py',
    'pc_discard.py', 'pc_receiver_cancel.py',
    'fast_button_service.py', 'fast_button_loader.py', 'fast_profile.json',
    'seal_fast_release.py', 'check_under10_release.py', 'current_geometry.json', 'calibration_binding.json',
    'zynq-button-scan.service.example', 'README.md',
    'persistent_evidence.py', 'persistent_camera_worker.py', 'persistent_camera.py', 'persistent_pl_camera.py',
    'benchmark_persistent_on_zynq.py',
    'object_board_scan_360.py', 'check_object_scan_plan_360.py', 'fullturn_closure.py', 'operator_clearance.json', 'reuse_authorization.json',
)
QUALITY = {'threshold': 12, 'min_run': 8, 'max_three_frame_range_px': 1.5,
           'minimum_common_columns': 10}
EXPECTED_PROFILE = {
    'schema_version': 1, 'version': VERSION, 'mode': 'diagnostic_preview',
    'roi_policy': 'explicit_in_image_rectangle_requires_static_scene_check', 'exposure': 5, 'max_angle': 360,
    'geometry_sha256': GEOMETRY, 'quality': QUALITY,
    'PS_quality_coordinate_reference': 'frozen_independent_integer_Q8',
    'original_images_per_scan': 225, 'PL_transactions_per_scan': 225,
    'acquisition_PS_crosschecks_required': True,
    'quality_review_mode': 'single_actual_PL_quality_recheck',
    'live_cross_implementation_regression_audit': False,
    'diagnostic_preview_only': True, 'alignment_validated': False,
    'metric_accuracy_accepted': False, 'current_geometry_revalidated': True,
    'calibration_scope': 'current glued checker axis0..360 rough preview; new0+12mm laser with independent33.5mm holdout; full object shape and metric accuracy unaccepted',
    'historical_extrinsics_reused': False, 'geometry_reuse_authorized': False,
    'laser_plane_preview_validated': True, 'axis_revalidation_skipped_by_user': False,
    'full_object_coverage_verified': False,
    'full_axis_validation_passed': True,
    'requested_angular_coverage_deg': 360, 'acquisition_step_deg': 5,
    'axis_extrapolation_beyond_measured60deg': False,
    'fullturn_closure_required': True,
    'camera_profile': 'persistent_manual_exposure_v1', 'camera_open_count_per_scan': 1,
    'exposure_restore_and_release': 'once_per_scan_with_final_proof',
    'capture_original_count_per_pose': 3, 'session_bright_originals': 6,
    'normal_return_strategy': 'complete_forward_full_turn_no_reverse',
    'unwrapped_feedback_rezeroed': False,
}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def ordinary_child(root, name):
    root = Path(root).resolve()
    require(isinstance(name, str) and name and '\\' not in name and ':' not in name,
            'Invalid candidate relative path')
    item = root / name
    require(not Path(name).is_absolute() and '..' not in Path(name).parts
            and item.resolve().is_relative_to(root), 'Candidate file escapes release')
    require(not item.is_symlink() and item.is_file(), 'Candidate requires an ordinary file: ' + name)
    return item


def verify_release(project, release, expected_manifest):
    """File-only inspection. A config/CLI-supplied manifest pin is mandatory."""
    project, release = Path(project).resolve(), Path(release).resolve()
    require(release != project and release.is_relative_to(project),
            'Fast release must be inside the selected project')
    require(isinstance(expected_manifest, str)
            and re.fullmatch('[0-9a-f]{64}', expected_manifest) is not None,
            'Fast candidate manifest SHA256 must be pinned')
    manifest_path = ordinary_child(release, 'SHA256.json')
    require(digest(manifest_path) == expected_manifest, 'Fast candidate manifest SHA256 changed')
    manifest = load(manifest_path)
    require(manifest.get('version') == VERSION and manifest.get('software_revision') == 1
            and manifest.get('kind') == 'software_candidate'
            and manifest.get('hardware_acceptance') is False
            and manifest.get('geometry_accuracy_accepted') is False
            and manifest.get('base_button_manifest_sha256') == BASE_MANIFEST
            and manifest.get('base_bit_sha256') == BASE_BIT
            and manifest.get('base_source_sha256') == BASE_SOURCES,
            'Fast candidate identity/acceptance/base bindings differ')
    rows = manifest.get('files')
    require(isinstance(rows, list) and len(rows) == len(REQUIRED_FILES),
            'Fast candidate manifest is incomplete')
    seen = set()
    for row in rows:
        require(isinstance(row, dict) and row.get('path') in REQUIRED_FILES
                and row['path'] not in seen, 'Unexpected or duplicate candidate file')
        seen.add(row['path'])
        item = ordinary_child(release, row['path'])
        require(type(row.get('bytes')) is int and item.stat().st_size == row['bytes']
                and digest(item) == row.get('sha256'), 'Fast candidate file changed: ' + row['path'])
    require(seen == set(REQUIRED_FILES), 'Fast candidate source bindings are incomplete')
    profile = load(release / 'fast_profile.json')
    # Compare exact JSON types as well as values: bool must not masquerade as int.
    require(json.dumps(profile, sort_keys=True) == json.dumps(EXPECTED_PROFILE, sort_keys=True),
            'Fast profile changes the acquisition, quality or diagnostic contract')
    require(digest(ordinary_child(release, 'current_geometry.json')) == GEOMETRY, 'Current geometry changed')
    require(load(ordinary_child(release, 'operator_clearance.json')).get('user_confirmed_full_turn_clearance') is True,
            'Operator full-turn clearance missing')
    from geometry_evidence import validate_geometry_evidence
    validate_geometry_evidence(load(release / 'current_geometry.json'))
    base = project / '17_button_scan_release_20261020'
    require(digest(ordinary_child(base, 'SHA256.json')) == BASE_MANIFEST,
            'Original 17 software revision 4 changed')
    require(digest(ordinary_child(base, 'laser_motor_button.bit')) == BASE_BIT,
            'Original button bitstream changed')
    for name, expected in BASE_SOURCES.items():
        require(digest(ordinary_child(project, name)) == expected,
                'Original bound source changed: ' + name)
    return {'version': VERSION, 'manifest_sha256': expected_manifest,
            'profile_sha256': digest(release / 'fast_profile.json'), 'profile': profile,
            'relative_directory': release.relative_to(project).as_posix(),
            'hardware_accessed': False, 'hardware_acceptance': False,
            'current_geometry_revalidated': True}
