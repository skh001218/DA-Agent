"""Run the full regression with disposable training data and recorder schema.

Execute inside the verification container with CASE_VERIFY_DATABASE pointing to
an empty database created for this run. No user package or training schema is used.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import psycopg
from psycopg.conninfo import make_conninfo
from da_agent.data import generate_package, validate_package
from da_agent.package_validation import generator_dsn


def main():
    database = os.environ['CASE_VERIFY_DATABASE']
    assert re.fullmatch(r'case_verify_[0-9a-f]{32}', database)
    generator = make_conninfo(generator_dsn(), dbname=database)
    with tempfile.TemporaryDirectory(prefix='case-regression-') as workspace:
        root = str(Path(workspace) / 'packages')
        project = Path(__file__).resolve().parents[2]
        for name in ('src', 'tests', 'scripts', 'pyproject.toml'):
            (Path(workspace) / name).symlink_to(project / name)
        with psycopg.connect(generator) as conn:
            for version, seed in [('v1', 20261003), ('v2', 20261004)]:
                generate_package(root, 'training-001', version, seed, 200)
                validate_package(conn, root, 'training-001', version)
        env = dict(os.environ, PACKAGES_ROOT=root, GENERATOR_DSN=generator,
                   LEARNER_DSN=make_conninfo(os.environ['LEARNER_DSN'], dbname=database))
        return subprocess.run([sys.executable, 'scripts/verification/verify_request_training.py',
                               'tests/automated'], env=env, cwd=workspace).returncode


if __name__ == '__main__':
    sys.exit(main())
