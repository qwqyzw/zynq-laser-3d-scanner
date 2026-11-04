"""Reassemble the validated execution layout from categorized source files."""
import hashlib
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'build' / 'runtime'


def prepare():
    manifest = json.loads((ROOT / 'build/source_map.json').read_text(encoding='utf-8'))
    verified = []
    for entry in manifest['files']:
        source = (ROOT / entry['source']).resolve()
        target = (RUNTIME / entry['original']).resolve()
        if not source.is_relative_to(ROOT) or not target.is_relative_to(RUNTIME):
            raise ValueError('File mapping escapes its designated directory')
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Source checksum mismatch: ' + entry['source'])
        verified.append((target, data))

    for target, data in verified:
        if target.is_file() and target.read_bytes() == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        try:
            temporary_path.replace(target)
        finally:
            temporary_path.unlink(missing_ok=True)
    return RUNTIME, len(verified)


if __name__ == '__main__':
    runtime, count = prepare()
    print(f'Verified and prepared {count} files in {runtime}')
