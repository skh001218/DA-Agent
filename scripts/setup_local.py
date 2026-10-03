"""Create local secrets once, without printing their values."""
import os
from pathlib import Path
import secrets
from cryptography.fernet import Fernet

root = Path(__file__).resolve().parents[1]
local = root / ".local"
local.mkdir(exist_ok=True)
env = root / ".env"
if not env.exists():
    env.write_text("\n".join(f"{key}={secrets.token_hex(24)}" for key in ("POSTGRES_PASSWORD", "LEARNER_PASSWORD", "RECORDER_PASSWORD")) + "\nWEB_PORT=8087\n", encoding="utf-8")
key_file = local / "auth.key"
if not key_file.exists():
    key_file.write_bytes(Fernet.generate_key())
    if os.name != "nt":
        key_file.chmod(0o600)
api_file = local / "gemini_api.key"
if not api_file.exists():
    api_file.touch(mode=0o600)
print("Local configuration ready. Existing secrets were preserved.")
