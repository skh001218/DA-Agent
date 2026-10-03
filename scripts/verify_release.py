"""Check regenerated release manifests against the reviewed Git fingerprints."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
expected = json.loads((root / "docs/release-fingerprints.json").read_text(encoding="utf-8"))
for name, fingerprint in expected.items():
    path = root / "packages" / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != fingerprint:
        raise SystemExit(f"Release fingerprint mismatch: {name}; do not publish")
print("Release manifest fingerprints match.")
