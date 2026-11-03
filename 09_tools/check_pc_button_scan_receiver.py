"""Offline receiver checks with fake SFTP and unchanged real board export bytes.

No SSH, browser, hardware, coordinate reconstruction, or invented point data.
Fixtures come from the retained 2026-10-20 board diagnostic export.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import posixpath
from types import SimpleNamespace
import stat
import tempfile

import pc_button_scan_receiver as receiver


ROOT = '/board/button_scan'
VIEWER = ROOT + '/runs/test_run/model/viewer'


class FakeSFTP:
    def __init__(self, files):
        self.files = dict(files)
        self.links = set()
        self.reads = {}
        self.change_manifest = False

    def lstat(self, path):
        if path in self.links:
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_size=0)
        if path in self.files:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=len(self.files[path]))
        if any(name.startswith(path.rstrip('/') + '/') for name in self.files):
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_size=0)
        raise FileNotFoundError(path)

    def open(self, path, mode):
        assert mode == 'rb'
        self.reads[path] = self.reads.get(path, 0) + 1
        data = self.files[path]
        if self.change_manifest and path.endswith('/export_manifest.json') and self.reads[path] > 1:
            data += b'\n'
            self.files[path] = data
        return io.BytesIO(data)


def main():
    source = Path(__file__).resolve().parents[1] / '16_zynq_object_scan_20261020/board_model/viewer'
    manifest_bytes = (source / 'export_manifest.json').read_bytes()
    manifest = receiver.json_bytes(manifest_bytes)
    files = {VIEWER + '/' + name: (source / name).read_bytes() for name in manifest['outputs']}
    files[VIEWER + '/export_manifest.json'] = manifest_bytes
    metadata = receiver.json_bytes((source / 'source_meta.json').read_bytes())
    record = {'schema_version': 1, 'session_id': 'offline_session', 'run_id': 'test_run', 'state': 'ready',
              'viewer_relative': posixpath.relpath(VIEWER, ROOT),
              'export_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
              **{name: metadata[name] for name in receiver.DIAGNOSTICS}}
    checks = []

    def expect_rejected(name, mutate=None, bad_record=None):
        sftp = FakeSFTP(files)
        if mutate:
            mutate(sftp)
        with tempfile.TemporaryDirectory(prefix='zynq_receiver_check_') as temp:
            try:
                receiver.download_export(sftp, ROOT, bad_record or record, Path(temp))
            except (ValueError, OSError):
                assert list(Path(temp).iterdir()) == [], 'Failed transfer left a visible/partial result'
                checks.append(name)
                return
            raise AssertionError('Expected rejection: ' + name)

    original_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    with tempfile.TemporaryDirectory(prefix='zynq_receiver_check_') as temp:
        result, receipt = receiver.download_export(FakeSFTP(files), ROOT, record, Path(temp))
        assert receipt['points'] == 1267 and receipt['diagnostic_preview_only'] is True
        assert receipt['alignment_validated'] is False and receipt['metric_accuracy_accepted'] is False
        assert receipt['pc_coordinate_computation'] is False
        for name in manifest['outputs']:
            assert hashlib.sha256((result / name).read_bytes()).hexdigest() == original_hashes[VIEWER + '/' + name]
        assert not any(item.name.startswith('.partial') for item in Path(temp).iterdir())
        checks.extend(['real_export_byte_for_byte', 'atomic_success_commit', 'diagnostic_flags_preserved'])
        try:
            receiver.download_export(FakeSFTP(files), ROOT, record, Path(temp))
        except ValueError:
            checks.append('duplicate_run_rejected')
        else:
            raise AssertionError('Duplicate result was overwritten')

    expect_rejected('tampered_PLY_rejected', lambda sftp: sftp.files.__setitem__(VIEWER + '/points.ply', b'tampered'))
    expect_rejected('missing_output_rejected', lambda sftp: sftp.files.pop(VIEWER + '/preview.html'))
    expect_rejected('remote_file_symlink_rejected', lambda sftp: sftp.links.add(VIEWER + '/points.ply'))
    expect_rejected('remote_directory_symlink_rejected', lambda sftp: sftp.links.add(ROOT + '/runs'))
    expect_rejected('manifest_changed_during_transfer_rejected', lambda sftp: setattr(sftp, 'change_manifest', True))
    expect_rejected('completion_hash_mismatch_rejected', bad_record={**record, 'export_manifest_sha256': '0' * 64})
    expect_rejected('diagnostic_conflict_rejected', bad_record={**record, 'alignment_validated': True})
    expect_rejected('path_traversal_rejected', bad_record={**record, 'viewer_relative': '../viewer'})
    expect_rejected('failed_scan_has_no_result', bad_record={**record, 'state': 'failed'})

    for path in ('a/../b', '/absolute', 'a\\b', 'C:/escape', 'CON.ply', 'source_images/a. ', 'a//b'):
        try:
            receiver.safe_relative(path)
        except ValueError:
            continue
        raise AssertionError('Unsafe path was accepted: ' + path)
    checks.append('cross_platform_path_guards')
    try:
        receiver.json_bytes(b'{"a":1,"a":2}')
    except ValueError:
        checks.append('duplicate_JSON_keys_rejected')
    else:
        raise AssertionError('Duplicate JSON keys accepted')
    assert {name: hashlib.sha256(data).hexdigest() for name, data in files.items()} == original_hashes
    report = {'status': 'passed', 'checks': checks, 'count': len(checks),
              'fixture': str(source), 'fixture_points': 1267,
              'coordinate_producer': 'zynq', 'original_files_modified': False,
              'real_network_used': False, 'hardware_accessed': False, 'display_opened': False,
              'pc_coordinate_computation': False}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
