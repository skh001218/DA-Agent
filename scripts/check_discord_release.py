"""Verify image source files against its recorded release before startup."""
import hashlib
import json
from pathlib import Path


def verify(root=None):
    root = root or Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'release.json').read_text(encoding='utf-8'))
    for name, digest in manifest['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Release source mismatch: ' + name)
    expected = {n for n in manifest['files'] if n.startswith('src/')}
    actual = {p.relative_to(root).as_posix() for p in (root / 'src').rglob('*')
              if p.is_file() and '__pycache__' not in p.parts}
    if actual != expected:
        raise RuntimeError('Release source file list changed')
    return manifest


if __name__ == '__main__':
    manifest = verify()
    print(json.dumps({'revision': manifest['revision'], 'source_ref': manifest['source_ref'],
                      'files_verified': len(manifest['files'])}))
