"""Explicit PC session: receive completed Zynq exports and present existing XYZ.

No scan, motor, overlay, geometry, alignment, or reconstruction commands are sent.
SSH host keys must already be trusted in known_hosts. Passwords are never saved.
Default display opens CloudCompare using a verified display-only BIN cache,
avoiding PLY import prompts. Close this foreground process with Ctrl+C to stop receiving.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import webbrowser


DEFAULT_REMOTE_ROOT = '/home/xilinx/jupyter_notebooks/button_scan'
MAX_MANIFEST = 2 * 1024 * 1024
MAX_RECORD = 256 * 1024
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
DIAGNOSTICS = ('diagnostic_preview_only', 'alignment_validated', 'metric_accuracy_accepted')
REQUIRED_OUTPUTS = {'points.ply', 'points.csv', 'source_meta.json', 'viewer_meta.json', 'preview.html'}
HEX = re.compile(r'^[0-9a-fA-F]{64}$')
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def json_bytes(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    result = json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique)
    require(isinstance(result, dict), 'Expected a JSON object')
    return result


def boolean(mapping, name, expected=None):
    value = mapping.get(name)
    require(type(value) is bool, 'Expected JSON boolean: ' + name)
    if expected is not None:
        require(value is expected, 'Unexpected metadata value: ' + name)
    return value


def safe_relative(value):
    require(isinstance(value, str) and bool(value), 'Missing relative path')
    require('\\' not in value and ':' not in value and '\x00' not in value,
            'Unsafe relative path: ' + repr(value))
    parts = PurePosixPath(value).parts
    require(not value.startswith('/') and value == '/'.join(parts), 'Non-canonical relative path')
    for part in parts:
        stem = part.split('.')[0].upper()
        require(part not in ('.', '..') and not part.endswith(('.', ' '))
                and not any(character in part for character in '<>"|?*')
                and not any(ord(character) < 32 for character in part)
                and stem not in {'CON', 'PRN', 'AUX', 'NUL', 'CLOCK$'}
                and not re.fullmatch(r'(COM|LPT)[1-9]', stem), 'Unsafe path component')
    return value


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def output_entries(manifest):
    outputs = manifest.get('outputs')
    require(isinstance(outputs, dict) and REQUIRED_OUTPUTS <= outputs.keys(), 'Incomplete viewer export')
    require(len(outputs) <= 4096, 'Too many output files')
    total, collision_names = 0, set()
    for name, entry in outputs.items():
        safe_relative(name)
        require(name.casefold() not in collision_names, 'Case-insensitive path collision')
        collision_names.add(name.casefold())
        require(isinstance(entry, dict), 'Invalid output record')
        size, digest = entry.get('bytes'), entry.get('sha256')
        require(type(size) is int and 0 <= size <= MAX_FILE, 'Invalid output byte size')
        require(isinstance(digest, str) and HEX.fullmatch(digest), 'Invalid output SHA256')
        total += size
    require(total <= MAX_TOTAL, 'Export exceeds the receive size limit')
    return outputs


def ordinary_local_file(root, relative):
    path = root
    parts = PurePosixPath(safe_relative(relative)).parts
    for index, part in enumerate(parts):
        path = path / part
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode) and not getattr(path.lstat(), 'st_file_attributes', 0) & 0x400,
                'Links/reparse points are not accepted')
        require(stat.S_ISREG(mode) if index == len(parts) - 1 else stat.S_ISDIR(mode),
                'Expected ordinary local file/directory')
    return path


def verify_ply(path, expected):
    require(type(expected) is int and expected > 0, 'Invalid finite point count')
    with path.open('r', encoding='ascii') as stream:
        require(stream.readline().rstrip('\r\n') == 'ply', 'Expected PLY')
        require(stream.readline().rstrip('\r\n') == 'format ascii 1.0', 'Expected ASCII PLY 1.0')
        count, properties = None, []
        for _ in range(1000):
            line = stream.readline().rstrip('\r\n')
            if line == 'end_header':
                break
            if line.startswith('comment '):
                continue
            if re.fullmatch(r'element vertex [0-9]+', line) and count is None:
                count = int(line.split()[-1])
            elif re.fullmatch(r'property (float|double) [xyz]', line):
                properties.append(line.split()[-1])
            else:
                raise ValueError('Unsupported/truncated PLY header')
        else:
            raise ValueError('PLY header too long')
        require(count == expected and properties == ['x', 'y', 'z'], 'PLY count/properties mismatch')
        rows = 0
        for line in stream:
            if not line.strip():
                continue
            values = line.split()
            require(len(values) == 3 and all(math.isfinite(float(value)) for value in values),
                    'PLY contains invalid XYZ')
            rows += 1
            require(rows <= expected, 'Too many PLY vertices')
        require(rows == expected, 'Missing PLY vertices')


def verify_export(directory, record=None):
    """Check existing coordinates; never calculate, transform, or write XYZ."""
    directory = Path(directory).resolve()
    manifest_path = ordinary_local_file(directory, 'export_manifest.json')
    require(manifest_path.stat().st_size <= MAX_MANIFEST, 'Manifest too large')
    original = manifest_path.read_bytes()
    manifest = json_bytes(original)
    require(manifest.get('data_origin') == 'hardware_scan'
            and manifest.get('coordinate_producer') == 'zynq', 'Not a hardware Zynq export')
    for name, expected in (('real_scan_export', True), ('test_only', False),
                           ('coordinate_computation_performed', False),
                           ('geometry_fitting_performed', False), ('surface_fitted', False)):
        boolean(manifest, name, expected)
    require(type(manifest.get('synthetic_points_added')) is int
            and manifest['synthetic_points_added'] == 0, 'Synthetic points are not accepted')
    platform = manifest.get('execution_platform')
    require(isinstance(platform, dict), 'Missing execution platform')
    boolean(platform, 'is_linux_arm_zynq_or_pynq', True)
    outputs = output_entries(manifest)
    for name, entry in outputs.items():
        path = ordinary_local_file(directory, name)
        require(path.stat().st_size == entry['bytes'] and sha256_file(path) == entry['sha256'].lower(),
                'Returned output changed or is incomplete: ' + name)
    source = json_bytes((directory / 'source_meta.json').read_bytes())
    viewer = json_bytes((directory / 'viewer_meta.json').read_bytes())
    units = manifest.get('units')
    require(isinstance(units, str) and bool(units.strip()), 'Missing units')
    for metadata in (source, viewer):
        require(metadata.get('data_origin') == 'hardware_scan'
                and metadata.get('coordinate_producer') == 'zynq', 'Contradictory provenance')
        boolean(metadata, 'synthetic', False)
        require(isinstance(metadata.get('geometry_status'), str), 'Missing geometry status')
        for name in DIAGNOSTICS:
            boolean(metadata, name)
    boolean(viewer, 'export_test_only', False)
    for name in ('geometry_status', *DIAGNOSTICS):
        require(source[name] == viewer[name], 'Source/viewer status mismatch: ' + name)
    for metadata in (manifest, source, viewer):
        require(metadata.get('units') == units and metadata.get('axis_units') == dict.fromkeys('xyz', units),
                'XYZ units do not match')
    statistics = manifest.get('point_statistics', {})
    count = statistics.get('finite_points')
    verify_ply(directory / 'points.ply', count)
    if record is not None:
        require(hashlib.sha256(original).hexdigest() == record['export_manifest_sha256'].lower(),
                'Export manifest does not match completion record')
        for name in DIAGNOSTICS:
            require(boolean(record, name) == source[name], 'Completion status mismatch: ' + name)
    require(manifest_path.read_bytes() == original, 'Manifest changed during validation')
    return {'points': count, 'units': units, 'geometry_status': source['geometry_status'],
            **{name: source[name] for name in DIAGNOSTICS},
            'coordinate_producer': 'zynq', 'pc_coordinate_computation': False,
            'manifest_sha256': hashlib.sha256(original).hexdigest()}


def check_remote_path(sftp, path, *, directory=False):
    require(path.startswith('/') and posixpath.normpath(path) == path, 'Expected canonical absolute remote path')
    current = ''
    parts = PurePosixPath(path).parts[1:]
    for index, part in enumerate(parts):
        current += '/' + part
        info = sftp.lstat(current)
        final = index == len(parts) - 1
        expected = stat.S_ISDIR if not final or directory else stat.S_ISREG
        require(expected(info.st_mode), 'Remote path is linked or not an ordinary file/directory')
    return info


def read_remote(sftp, path, limit):
    info = check_remote_path(sftp, path)
    require(0 <= info.st_size <= limit, 'Remote control file too large')
    with sftp.open(path, 'rb') as stream:
        result = stream.read(limit + 1)
    require(len(result) == info.st_size and len(result) <= limit, 'Remote control file changed')
    return result


def record_identity(record):
    require(record.get('schema_version') == 1, 'Unknown completion schema')
    for name in ('session_id', 'run_id'):
        require(isinstance(record.get(name), str) and IDENTIFIER.fullmatch(record[name]), 'Invalid ' + name)
    require(record.get('state') in ('ready', 'failed'), 'Unknown completion state')
    if record['state'] == 'ready':
        safe_relative(record.get('viewer_relative'))
        digest = record.get('export_manifest_sha256')
        require(isinstance(digest, str) and HEX.fullmatch(digest), 'Invalid completion manifest SHA256')
        for name in DIAGNOSTICS:
            boolean(record, name)
    return record['session_id'] + '_' + record['run_id']


def download_export(sftp, remote_root, record, output, *, reuse_existing=False):
    """Stage and validate a completed immutable board export, then commit."""
    identity = record_identity(record)
    require(record['state'] == 'ready', 'Only a ready export can be received')
    viewer = remote_root + '/' + record['viewer_relative']
    manifest_path = viewer + '/export_manifest.json'
    original = read_remote(sftp, manifest_path, MAX_MANIFEST)
    require(hashlib.sha256(original).hexdigest() == record['export_manifest_sha256'].lower(),
            'Board manifest does not match completion pointer')
    manifest = json_bytes(original)
    outputs = output_entries(manifest)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / identity
    if destination.exists() and reuse_existing:
        require(destination.is_dir() and not destination.is_symlink()
                and not getattr(destination.lstat(), 'st_file_attributes', 0) & 0x400,
                'Existing result is not an ordinary directory')
        existing_viewer = destination / 'viewer'
        require(existing_viewer.is_dir() and not existing_viewer.is_symlink()
                and not getattr(existing_viewer.lstat(), 'st_file_attributes', 0) & 0x400,
                'Existing viewer is not an ordinary directory')
        saved_record = json_bytes(ordinary_local_file(destination, 'completion_received.json').read_bytes())
        require(saved_record == record, 'Existing completion differs from the current board completion')
        receipt = verify_export(existing_viewer, record)
        return existing_viewer, {**receipt, 'existing_verified_result_reused': True}
    require(not destination.exists(), 'A result with this identity already exists')
    stage = Path(tempfile.mkdtemp(prefix='.partial-' + identity + '-', dir=output))
    try:
        local_viewer = stage / 'viewer'
        local_viewer.mkdir()
        for name, entry in outputs.items():
            remote = viewer + '/' + name
            before = check_remote_path(sftp, remote)
            require(before.st_size == entry['bytes'], 'Board output size changed: ' + name)
            local = local_viewer.joinpath(*PurePosixPath(name).parts)
            local.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with sftp.open(remote, 'rb') as source, local.open('xb') as target:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    require(size <= entry['bytes'], 'Board output grew during transfer: ' + name)
                    digest.update(block)
                    target.write(block)
            after = check_remote_path(sftp, remote)
            require(size == entry['bytes'] == after.st_size
                    and digest.hexdigest() == entry['sha256'].lower(), 'Board output SHA/size mismatch: ' + name)
        require(read_remote(sftp, manifest_path, MAX_MANIFEST) == original, 'Board manifest changed during transfer')
        (local_viewer / 'export_manifest.json').write_bytes(original)
        receipt = verify_export(local_viewer, record)
        (stage / 'completion_received.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        (stage / 'pc_receive_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        stage.rename(destination)
        return destination / 'viewer', receipt
    except BaseException:
        shutil.rmtree(stage)
        raise


def present(directory, mode):
    directory = Path(directory).resolve()
    verified = verify_export(directory)
    receipt = {**verified, 'display_mode': mode, 'viewer': str(directory), 'gui_launched': False}
    if mode == 'none':
        pass
    elif mode == 'html':
        require(webbrowser.open((directory / 'preview.html').as_uri(), new=2), 'Default browser did not accept preview')
        receipt['gui_launched'] = True
    else:
        require(mode == 'cloudcompare', 'Unknown display mode')
        script = Path(__file__).with_name('open_zynq_cloudcompare_auto.ps1')
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            completed = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                                       '-ResultDirectory', str(directory)], check=True, capture_output=True,
                                       text=True, encoding='utf-8', errors='replace', timeout=50)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or str(error)).strip()[-1600:]
            raise ValueError('CloudCompare display failed: ' + detail) from None
        opened = json_bytes(completed.stdout.encode('utf-8'))
        require(opened.get('gui_launched') is True and opened.get('pc_coordinate_computation') is False,
                'CloudCompare helper did not confirm a display-only launch')
        require(opened.get('manifest_sha256') == verified['manifest_sha256']
                and opened.get('points') == verified['points'], 'Display receipt differs from validated board data')
        receipt.update(opened)
    # Keep operational receipts outside the immutable returned viewer/model.
    base = Path(os.environ['LOCALAPPDATA']) if os.environ.get('LOCALAPPDATA') else Path.home() / '.cache'
    receipt_directory = base / 'ZynqPointCloud/display_receipts' / verified['manifest_sha256']
    receipt_directory.mkdir(parents=True, exist_ok=True)
    target = receipt_directory / 'last_display.json'
    temporary = receipt_directory / ('display-' + str(os.getpid()) + '.writing')
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, target)
    return receipt


def connect(args, password):
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError('This Python environment needs paramiko; use the configured project launcher.') from exc
    client = paramiko.SSHClient()
    if args.known_hosts:
        client.load_host_keys(str(args.known_hosts.expanduser()))
    else:
        client.load_system_host_keys()
        user_hosts = Path.home() / '.ssh/known_hosts'
        if user_hosts.is_file():
            client.load_host_keys(str(user_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(args.host, port=args.port, username=args.username, password=password,
                       key_filename=str(args.identity_file.expanduser()) if args.identity_file else None,
                       look_for_keys=not bool(password), allow_agent=not bool(password),
                       timeout=10, auth_timeout=10, banner_timeout=10)
        return client
    except (paramiko.AuthenticationException, paramiko.BadHostKeyException) as error:
        client.close()
        raise ValueError('SSH authentication or trusted host identity rejected: ' + type(error).__name__) from None
    except paramiko.SSHException as error:
        client.close()
        if 'not found in known_hosts' in str(error):
            raise ValueError('SSH host key is not trusted; complete the documented setup first') from None
        raise OSError('SSH transport unavailable: ' + type(error).__name__) from None
    except BaseException:
        client.close()
        raise


def run(args):
    try:
        import paramiko
    except ImportError:
        raise RuntimeError('SSH Python dependencies are missing; use the configured project launcher') from None
    require(args.remote_root.startswith('/') and posixpath.normpath(args.remote_root) == args.remote_root,
            '--remote-root must be a canonical absolute path')
    password = None
    if args.password_env:
        password = os.environ.get(args.password_env)
        require(bool(password), 'Password environment variable is absent or empty')
    elif not args.identity_file:
        password = getpass.getpass('Zynq SSH password (not saved): ')
    initialized, seen, failures = False, set(), set()
    print('Receiver active. PC only receives/displays existing Zynq XYZ. Ctrl+C exits.', flush=True)
    while True:
        client = None
        try:
            client = connect(args, password)
            with client.open_sftp() as sftp:
                check_remote_path(sftp, args.remote_root, directory=True)
                while True:
                    try:
                        raw = read_remote(sftp, args.remote_root + '/latest_result.json', MAX_RECORD)
                    except FileNotFoundError:
                        # No completion yet: the next published result is new.
                        initialized = True
                        if args.once:
                            return 0
                        time.sleep(args.poll_seconds)
                        continue
                    record = json_bytes(raw)
                    identity = record_identity(record)
                    if not initialized:
                        initialized = True
                        if not args.include_existing:
                            seen.add(identity)
                            print('Existing completion ignored; waiting for the next button scan.', flush=True)
                    if identity not in seen and identity not in failures:
                        if record['state'] == 'failed':
                            failures.add(identity)
                            print('Board scan failed; no old model is displayed. Run: ' + identity, flush=True)
                        else:
                            try:
                                directory, receipt = download_export(sftp, args.remote_root, record, args.output,
                                                                     reuse_existing=args.include_existing)
                                print(json.dumps({'received': str(directory), **receipt}, ensure_ascii=False), flush=True)
                                display_receipt = present(directory, args.display)
                                print(json.dumps({'presented': str(directory), **display_receipt}, ensure_ascii=False), flush=True)
                                seen.add(identity)
                            except (ValueError, subprocess.SubprocessError) as exc:
                                failures.add(identity)
                                print('Receive/display rejected for ' + identity + ': ' + str(exc), file=sys.stderr, flush=True)
                    if args.once:
                        return 1 if identity in failures else 0
                    time.sleep(args.poll_seconds)
        except (OSError, EOFError, paramiko.SSHException) as exc:
            if args.once:
                raise
            print('Connection unavailable; waiting to reconnect: ' + type(exc).__name__, file=sys.stderr, flush=True)
            time.sleep(max(args.poll_seconds, 3))
        finally:
            if client is not None:
                client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.2.99')
    parser.add_argument('--port', type=int, default=22)
    parser.add_argument('--username', default='xilinx')
    parser.add_argument('--known-hosts', type=Path)
    parser.add_argument('--identity-file', type=Path)
    parser.add_argument('--password-env', help='Name of an existing environment variable; never a literal password')
    parser.add_argument('--remote-root', default=DEFAULT_REMOTE_ROOT)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'results/button_scan_received')
    parser.add_argument('--poll-seconds', type=float, default=1)
    parser.add_argument('--display', choices=('html', 'cloudcompare', 'none'), default='cloudcompare')
    parser.add_argument('--include-existing', action='store_true', help='Explicitly receive an already published completion')
    parser.add_argument('--once', action='store_true', help='Inspect one completion snapshot and exit')
    parser.add_argument('--validate-only', type=Path, metavar='VIEWER', help='Offline export validation; no SSH or display')
    args = parser.parse_args()
    try:
        if args.validate_only:
            print(json.dumps(verify_export(args.validate_only), ensure_ascii=False, indent=2))
            return 0
        require(0.25 <= args.poll_seconds <= 60, 'Polling interval must be 0.25..60 seconds')
        return run(args)
    except KeyboardInterrupt:
        print('\nReceiver stopped. No hardware commands were sent.')
        return 0
    except Exception as exc:
        # Do not print connection arguments/passwords or provider internals.
        print('Receiver stopped: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
