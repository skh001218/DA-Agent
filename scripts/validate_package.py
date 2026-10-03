"""Validate an existing package with PostgreSQL before it can be offered."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from da_agent.data import validate_package

if __name__ == "__main__":
    import psycopg
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="packages")
    parser.add_argument("--package-id", default="training-001")
    parser.add_argument("--version", default="v1")
    parser.add_argument("--dsn", default=os.getenv("ADMIN_DATABASE_URL") or os.getenv("DATABASE_URL"))
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or ADMIN_DATABASE_URL is required")
    with psycopg.connect(args.dsn) as conn:
        package = validate_package(conn, args.root, args.package_id, args.version)
    print(f"Publishable: {package.public['package_id']} / {package.public['release_version']}")
