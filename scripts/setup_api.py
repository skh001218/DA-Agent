"""Run directly in your terminal; never supply secrets as command arguments."""
import getpass
import os
from pathlib import Path
import tempfile
import warnings


def main():
    root = Path(__file__).resolve().parents[1]
    env = root / ".env"
    if not env.exists():
        raise SystemExit("Run scripts/setup_local.py first.")
    warnings.simplefilter("error", getpass.GetPassWarning)
    key = getpass.getpass("Gemini API key (hidden input): ").strip()
    if len(key) < 20 or any(char.isspace() for char in key):
        raise SystemExit("No settings changed. Enter a valid API key.")
    local = root / ".local"
    local.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(dir=local, prefix=".api-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(key)
        os.replace(name, local / "gemini_api.key")
    finally:
        Path(name).unlink(missing_ok=True)
    lines = env.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith("DA_LLM_PROVIDER=")]
    lines.append("DA_LLM_PROVIDER=gemini")
    if not any(line.startswith("GEMINI_MODEL=") for line in lines):
        lines.append("GEMINI_MODEL=gemini-3.8-flash")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("API key saved locally. Run: docker compose up -d --build app")


if __name__ == "__main__":
    main()
