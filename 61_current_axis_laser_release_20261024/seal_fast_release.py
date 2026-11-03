"""Explicitly seal a new unaccepted software candidate; never touches hardware."""
import argparse
import hashlib
import json
from pathlib import Path


def seal(release):
    from fast_release import VERSION, REQUIRED_FILES, BASE_MANIFEST, BASE_BIT, BASE_SOURCES
    release = Path(release).resolve()
    files = []
    for name in REQUIRED_FILES:
        path = release / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Candidate requires an ordinary file: ' + name)
        data = path.read_bytes()
        files.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'version': VERSION, 'software_revision': 1, 'kind': 'software_candidate',
                'hardware_acceptance': False, 'geometry_accuracy_accepted': False,
                'base_button_manifest_sha256': BASE_MANIFEST, 'base_bit_sha256': BASE_BIT,
                'base_source_sha256': BASE_SOURCES, 'files': files}
    data = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (release / 'SHA256.json').write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seal-candidate', action='store_true')
    args = parser.parse_args()
    if not args.seal_candidate:
        print(json.dumps({'status': 'not_sealed', 'hardware_accessed': False}))
        return
    value = seal(Path(__file__).resolve().parent)
    print(json.dumps({'status': 'unaccepted_candidate_fingerprinted',
                      'fast_manifest_sha256': value, 'hardware_accessed': False,
                      'hardware_acceptance': False}))


if __name__ == '__main__':
    main()
