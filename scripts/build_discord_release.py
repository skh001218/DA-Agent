"""Create a source fingerprint manifest inside the image, without secrets."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def fingerprints(root):
    files = [p for p in (root / 'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files.extend(root / p for p in ('requirements.lock.txt', 'requirements-discord.txt',
        'scripts/run_discord_container.py', 'scripts/check_discord_release.py', 'scripts/build_discord_release.py'))
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--revision', required=True)
    parser.add_argument('--source-ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.revision):
        raise SystemExit('GIT_SHA must identify the full source commit; use scripts/deploy_discord.py')
    root = Path(__file__).resolve().parents[1]
    manifest = dict(revision=args.revision, source_ref=args.source_ref,
        built_at=datetime.now(timezone.utc).isoformat(), files=fingerprints(root))
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
    print('Release manifest created for ' + args.revision)


if __name__ == '__main__':
    main()
