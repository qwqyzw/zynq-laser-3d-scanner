"""Versioned launch routing; frozen acquisition/GPIO/stop/poll are inherited."""
import argparse
import json
from pathlib import Path
import sys
import uuid

RELEASE = Path(__file__).resolve().parent
PROJECT = RELEASE.parent
sys.path.insert(0, str(RELEASE))
sys.path.insert(0, str(PROJECT / '09_tools'))
from fast_release import VERSION, BASE_MANIFEST, BASE_BIT, GEOMETRY, digest, load, require, verify_release
import board_button_scan_service as original_service

BASE_CONFIG_KEYS = {'project', 'overlay_release', 'geometry', 'geometry_sha256',
                    'output_root', 'client_date', 'mode', 'roi', 'exposure', 'max_angle'}
CONFIG_KEYS = BASE_CONFIG_KEYS | {'fast_release', 'fast_manifest_sha256'}


def inspect_config(path):
    from datetime import date
    raw = load(path)
    require(set(raw) == CONFIG_KEYS, 'Current configuration fields differ')
    config = dict(raw)
    for name in ('project', 'overlay_release', 'geometry', 'output_root', 'fast_release'):
        require(Path(raw[name]).is_absolute(), 'Absolute path required: ' + name)
        config[name] = Path(raw[name]).resolve()
    require(config['project'] == PROJECT and config['fast_release'] == RELEASE,
            'Configuration belongs to another project/release')
    require(date.fromisoformat(raw['client_date']).isoformat() == raw['client_date'], 'Invalid client date')
    fast = verify_release(PROJECT, RELEASE, raw['fast_manifest_sha256'])
    require(Path(original_service.__file__).resolve() == PROJECT / '09_tools/board_button_scan_service.py',
            'Original coordinator belongs to another project')
    for key in ('mode', 'exposure', 'max_angle', 'geometry_sha256'):
        require(type(raw[key]) is type(fast['profile'][key]) and raw[key] == fast['profile'][key],
                'Configuration differs from current profile: ' + key)
    roi = raw['roi']
    require(isinstance(roi, list) and len(roi) == 4 and all(type(v) is int for v in roi)
            and 0 <= roi[0] < roi[2] <= 640 and 0 <= roi[1] < roi[3] <= 480, 'Invalid explicit ROI')
    require(config['geometry'] == RELEASE / 'current_geometry.json'
            and digest(config['geometry']) == GEOMETRY, 'Current geometry pin differs')
    import object_board_scan_360 as object_board_scan
    _, artifacts, _ = object_board_scan.inspect(PROJECT, raw['max_angle'], tuple(roi), raw['exposure'],
                                               button_release=config['overlay_release'])
    require(config['overlay_release'] == PROJECT / '17_button_scan_release_20261020'
            and artifacts['button_release']['manifest_sha256'] == BASE_MANIFEST
            and artifacts['button_release']['laser_motor_button.bit']['sha256'] == BASE_BIT,
            'Current runtime requires unchanged 17 revision 4')
    from fast_reconstruct_on_zynq import diagnostic_geometry_evidence
    diagnostic_geometry_evidence(load(config['geometry']))
    artifacts['fast_release'] = fast
    return config, artifacts


def route_args(args, stage, config, manifest_sha256):
    """Only the expected reconstruction executable is substituted."""
    args = list(args)
    require(len(args) >= 4 and all(isinstance(value, str) for value in args), 'Invalid child argv')
    project = Path(config['project'])
    if stage == 'acquiring':
        require(args[3] == str(project / '09_tools/object_board_scan.py'),
                'Unexpected acquisition executable')
        args[3] = str(Path(config['fast_release']) / 'object_board_scan_360.py')
        args.extend(['--fast-manifest-sha256', manifest_sha256])
        return args
    require(stage == 'reconstructing_on_zynq', 'Unknown pipeline stage')
    expected = str(project / '09_tools/reconstruct_object_on_zynq.py')
    require(args[3] == expected and args.count(expected) == 1
            and '--fast-manifest-sha256' not in args and args.count('--run-board') == 1
            and args.count('--diagnostic-preview') == 1,
            'Unexpected reconstruction argv')
    args[3] = str(Path(config['fast_release']) / 'fast_reconstruct_on_zynq.py')
    args.extend(['--fast-manifest-sha256', manifest_sha256])
    return args


from cancel_discard import CancelDiscardMixin

class Coordinator(CancelDiscardMixin, original_service.Coordinator):
    def verified_current(self):
        require(digest(self.config['config_file']) == self.config_sha256,
                'Configuration changed; restart service explicitly')
        config, artifacts = inspect_config(self.config['config_file'])
        for name in ('button_release', 'fast_release'):
            require(artifacts[name]['manifest_sha256'] == self.artifacts[name]['manifest_sha256'],
                    'Bound release changed while service was armed: ' + name)
        return config, artifacts

    def start(self):
        if self.process is not None or self.exiting:
            return
        self.verified_current()
        if digest(self.config['config_file']) != self.config_sha256:
            raise ValueError('Configuration changed; restart service explicitly')
        # Recheck immutable runtime/geometry before any child can touch hardware.
        _, artifacts = inspect_config(self.config['config_file'])
        if artifacts['button_release']['manifest_sha256'] != self.artifacts['button_release']['manifest_sha256']:
            raise ValueError('Button release changed while service was armed')
        self.run_id = uuid.uuid4().hex
        self.run = self.root / 'runs' / self.run_id
        self.run.mkdir(parents=True, exist_ok=False)
        self.cancelled = False
        project = self.config['project']
        args = [sys.executable, '-B', '-u', str(project / '09_tools/object_board_scan.py'),
                '--project', str(project), '--button-release', str(self.config['overlay_release']),
                '--output', str(self.run / 'scan'), '--client-date', self.config['client_date'],
                '--roi', *map(str, self.config['roi']), '--exposure', str(self.config['exposure']),
                '--max-angle', str(self.config['max_angle']), '--run-board']
        self.launch(args, 'acquiring')


    def launch(self, args, stage):
        config, artifacts = self.verified_current()
        routed = route_args(args, stage, config, artifacts['fast_release']['manifest_sha256'])
        if stage == 'acquiring':
            original_service.save(self.run / 'fast_software_binding.json', {
                'version': VERSION,
                'fast_manifest_sha256': artifacts['fast_release']['manifest_sha256'],
                'profile_sha256': artifacts['fast_release']['profile_sha256'],
                'base_button_manifest_sha256': BASE_MANIFEST,
                'acquisition_script_unchanged': False, 'acquisition_transaction_logic_preserved': True, 'acquisition_PS_crosschecks_required': True,
                'original_images_per_scan': 225, 'PL_transactions_per_scan': 225,
                'current_geometry_revalidated': True, 'diagnostic_preview_only': True,
                'alignment_validated': False, 'metric_accuracy_accepted': False,
                'historical_extrinsics_reused': False, 'geometry_reuse_authorized': False,
                'laser_plane_preview_validated': True, 'axis_revalidation_skipped_by_user': False,
                'camera_profile': 'persistent_manual_exposure_v1', 'camera_open_count_per_scan': 1,
                'normal_return_strategy': 'complete_forward_full_turn_no_reverse',
                'full_axis_validation_passed': True, 'axis_extrapolation_beyond_measured60deg': False})
        return super().launch(routed, stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--serve-board', action='store_true')
    args = parser.parse_args()
    config, artifacts = inspect_config(args.config)
    config['config_file'] = args.config.resolve()
    if not args.serve_board:
        print(json.dumps({'status': 'fast_button_configuration_verified', 'version': VERSION,
                          'fast_manifest_sha256': artifacts['fast_release']['manifest_sha256'],
                          'hardware_accessed': False, 'hardware_acceptance': False}))
        return 0
    Coordinator(config, artifacts).serve()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('Fast button startup rejected: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
