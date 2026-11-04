"""Offline checks for automatic display, verified reuse and cache rejection.

Uses unchanged real board exports. PowerShell is only called in ValidateOnly or
VerifyCacheOnly modes. No SSH, CloudCompare, browser, motor, or XYZ computation.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pc_button_scan_receiver as receiver
from check_pc_button_scan_receiver import FakeSFTP

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '09_tools/open_zynq_cloudcompare_auto.ps1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    source = ROOT / '26_button_end_to_end_20261021/raw_run/model/viewer'
    if not source.is_dir():
        source = ROOT / '16_zynq_object_scan_20261020/board_model/viewer'
    before = {p.relative_to(source).as_posix(): digest(p) for p in source.rglob('*') if p.is_file()}
    verified = receiver.verify_export(source)
    checks = []

    def check(name, condition, detail=''):
        if not condition:
            raise AssertionError(name + (': ' + detail if detail else ''))
        checks.append(name)

    def reject(name, action):
        try:
            action()
        except (ValueError, OSError):
            checks.append(name)
        else:
            raise AssertionError(name + ' was accepted')

    remote_root = '/board/button_scan'
    remote_viewer = remote_root + '/runs/run/model/viewer'
    original = (source / 'export_manifest.json').read_bytes()
    manifest = receiver.json_bytes(original)
    files = {remote_viewer + '/' + name: (source / name).read_bytes() for name in manifest['outputs']}
    files[remote_viewer + '/export_manifest.json'] = original
    record = {'schema_version': 1, 'session_id': 'session', 'run_id': 'run', 'state': 'ready',
              'viewer_relative': 'runs/run/model/viewer', 'export_manifest_sha256': verified['manifest_sha256'],
              **{name: verified[name] for name in receiver.DIAGNOSTICS}}
    with tempfile.TemporaryDirectory(prefix="auto_zynq 中文 & '") as temporary:
        temp = Path(temporary)
        local, _ = receiver.download_export(FakeSFTP(files), remote_root, record, temp / 'received')
        reused, receipt = receiver.download_export(FakeSFTP(files), remote_root, record,
                                                   temp / 'received', reuse_existing=True)
        check('explicit_existing_result_revalidated_and_reused', reused == local and receipt['existing_verified_result_reused'])
        reject('duplicate_still_rejected_without_explicit_reuse',
               lambda: receiver.download_export(FakeSFTP(files), remote_root, record, temp / 'received'))
        saved = local.parent / 'completion_received.json'
        saved_bytes = saved.read_bytes()
        changed = {**record, 'alignment_validated': True}
        saved.write_text(json.dumps(changed), encoding='utf-8')
        reject('existing_completion_change_rejected',
               lambda: receiver.download_export(FakeSFTP(files), remote_root, record, temp / 'received', reuse_existing=True))
        saved.write_bytes(saved_bytes)
        ply = local / 'points.ply'
        ply_bytes = ply.read_bytes()
        ply.write_bytes(ply_bytes + b'0 0 0\n')
        reject('existing_PLY_change_rejected',
               lambda: receiver.download_export(FakeSFTP(files), remote_root, record, temp / 'received', reuse_existing=True))
        ply.write_bytes(ply_bytes)

        opened = {'gui_launched': True, 'pc_coordinate_computation': False,
                  'manifest_sha256': verified['manifest_sha256'], 'points': verified['points'],
                  'format_conversion_only': True}
        env = {**os.environ, 'LOCALAPPDATA': str(temp / 'local appdata')}
        with patch.dict(os.environ, env, clear=True), patch.object(receiver.subprocess, 'run') as launch:
            launch.return_value = SimpleNamespace(stdout=json.dumps(opened))
            receipt = receiver.present(local, 'cloudcompare')
            call = launch.call_args
            argv = call.args[0]
            check('native_helper_argv_preserves_unicode_spaces_and_metacharacters',
                  argv[-1] == str(local.resolve()) and argv[-3].endswith('open_zynq_cloudcompare_auto.ps1')
                  and call.kwargs['capture_output'] and call.kwargs['timeout'] == 50)
            check('display_receipt_saved_outside_source_viewer',
                  (temp / 'local appdata/ZynqPointCloud/display_receipts' / verified['manifest_sha256'] / 'last_display.json').is_file()
                  and not (local / 'last_display.json').exists())
            check('successful_launch_receipt_returned', receipt['gui_launched'] and receipt['format_conversion_only'])
            launch.return_value = SimpleNamespace(stdout=json.dumps({**opened, 'points': verified['points'] + 1}))
            reject('helper_receipt_wrong_point_count_rejected', lambda: receiver.present(local, 'cloudcompare'))
            launch.side_effect = subprocess.CalledProcessError(1, ['display-helper'], stderr='conversion failed; log retained')
            try:
                receiver.present(local, 'cloudcompare')
            except ValueError as error:
                check('helper_failure_retains_useful_local_error', 'conversion failed; log retained' in str(error))
            else:
                raise AssertionError('Helper failure was ignored')

        with patch.object(sys, 'argv', ['receiver']), patch.object(receiver, 'run', return_value=0) as run:
            check('receiver_default_cli_succeeds_without_connecting', receiver.main() == 0)
            parsed = run.call_args.args[0]
            check('cloudcompare_and_one_second_are_defaults', parsed.display == 'cloudcompare' and parsed.poll_seconds == 1)
        with patch.object(sys, 'argv', ['receiver', '--display', 'html']), patch.object(receiver, 'run', return_value=0) as run:
            receiver.main()
            check('explicit_HTML_mode_remains_available', run.call_args.args[0].display == 'html')

        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'

        def ps(*extra):
            return subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
                                   '-ResultDirectory', str(local), *map(str, extra)],
                                  capture_output=True, encoding='utf-8', errors='replace', timeout=20)

        cache_root = temp / 'display cache'
        result = ps('-CacheRoot', cache_root, '-ValidateOnly')
        checked = json.loads(result.stdout) if result.returncode == 0 else {}
        check('PS5_validate_only_checks_real_export_without_GUI_or_cache',
              result.returncode == 0 and checked['points'] == verified['points']
              and checked['gui_launched'] is False and not cache_root.exists())

        # A nonexecuted ordinary executable is enough to test its SHA binding.
        executable = temp / 'program/CloudCompare.exe'
        executable.parent.mkdir()
        shutil.copyfile(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/where.exe', executable)
        forbidden_cache = local / 'display_cache_must_not_be_created'
        result = ps('-CloudComparePath', executable, '-CacheRoot', forbidden_cache, '-VerifyCacheOnly')
        check('cache_cannot_be_created_inside_original_viewer', result.returncode != 0 and not forbidden_cache.exists())
        cache = cache_root / verified['manifest_sha256']
        build_name = 'build_' + 'a' * 32
        build = cache / build_name
        build.mkdir(parents=True)
        binary = build / 'Zynq_DIAGNOSTIC_UNVERIFIED.bin'
        actual_bin = ROOT / 'results/auto_cloudcompare_20261021/cli_probe/Zynq_DIAGNOSTIC_UNVERIFIED.bin'
        if actual_bin.exists():
            shutil.copyfile(actual_bin, binary)
        else:
            binary.write_bytes(b'cache-integrity-fixture-no-program-will-open-this')
        cache_record = {'schema_version': 1, 'manifest_sha256': verified['manifest_sha256'],
                        'source_ply_sha256': digest(ply), 'points': verified['points'],
                        **{name: verified[name] for name in receiver.DIAGNOSTICS},
                        'cloudcompare_executable_sha256': digest(executable),
                        'binary_relative': build_name + '/' + binary.name,
                        'binary_sha256': digest(binary), 'binary_bytes': binary.stat().st_size,
                        'conversion_seconds': 0.0, 'format_conversion_only': True, 'pc_coordinate_computation': False}
        record_path = cache / 'display_cache.json'
        record_path.write_text(json.dumps(cache_record), encoding='utf-8')
        options = ('-CloudComparePath', executable, '-CacheRoot', cache_root, '-VerifyCacheOnly')
        result = ps(*options)
        cached = json.loads(result.stdout) if result.returncode == 0 else {}
        check('valid_cache_reused_without_starting_any_CloudCompare',
              result.returncode == 0 and cached['cache_reused'] and not cached['gui_launched'], result.stderr)
        cached_bytes = binary.read_bytes()
        binary.write_bytes(cached_bytes + b'changed')
        check('tampered_BIN_cache_rejected', ps(*options).returncode != 0)
        binary.write_bytes(cached_bytes)
        record_path.write_text(json.dumps({**cache_record, 'binary_relative': '../outside.bin'}), encoding='utf-8')
        check('escaping_BIN_cache_path_rejected', ps(*options).returncode != 0)
        record_path.write_text(json.dumps({**cache_record, 'source_ply_sha256': '0' * 64}), encoding='utf-8')
        check('wrong_source_BIN_cache_rejected', ps(*options).returncode != 0)
        record_path.write_text(json.dumps(cache_record), encoding='utf-8')
        ply.write_bytes(ply_bytes + b'changed')
        check('source_corruption_not_hidden_by_valid_cache', ps(*options).returncode != 0)
        ply.write_bytes(ply_bytes)

    after = {p.relative_to(source).as_posix(): digest(p) for p in source.rglob('*') if p.is_file()}
    check('original_board_fixture_files_unchanged', before == after)
    report = {'status': 'passed', 'checks': checks, 'count': len(checks), 'fixture': str(source),
              'fixture_points': verified['points'], 'hardware_accessed': False, 'network_used': False,
              'CloudCompare_started': False, 'browser_started': False, 'pc_coordinate_computation': False}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
