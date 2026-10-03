"""Generate a new immutable training package. Run from repository root."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from da_agent.data import generate_package

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="packages")
    parser.add_argument("--package-id", default="training-001")
    parser.add_argument("--version", default="v1")
    parser.add_argument("--seed", type=int, default=20261003)
    parser.add_argument("--users", type=int, default=200)
    args = parser.parse_args()
    package = generate_package(args.root, args.package_id, args.version, args.seed, args.users)
    print(f"Created {package.path}; pending PostgreSQL validation")
