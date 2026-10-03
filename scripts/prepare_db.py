"""Provision validated immutable training schemas; never reset learner records."""
import os
from pathlib import Path
import psycopg
from da_agent.packages import PackageCatalog
from da_agent.data import load_package, validate_package

root = Path(os.getenv("PACKAGES_ROOT", "packages"))
catalog = PackageCatalog(root)
with psycopg.connect(os.environ["ADMIN_DSN"]) as conn:
    for public in root.glob("*/*/public/manifest.json"):
        version, package_id = public.parents[1].name, public.parents[2].name
        package = catalog.load(package_id, version, allow_unvalidated=True)
        load_package(conn, package)
        conn.commit()
        validate_package(conn, root, package_id, version)
        conn.commit()
        print(f"Validated: {package_id}/{version}")
