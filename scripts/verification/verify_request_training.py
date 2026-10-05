"""Run DB regression tests in a disposable recorder schema, preserving user records."""
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


def main():
    dsn = os.environ['RECORDS_DSN']
    schema = 'verify_request_' + uuid.uuid4().hex
    assert re.fullmatch(r'verify_request_[0-9a-f]{32}', schema)
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    env = dict(os.environ, RUN_DB_TESTS='1', RECORDS_DSN=make_conninfo(dsn, options=f'-c search_path={schema}'))
    tests = Path(sys.argv[1] if len(sys.argv) > 1 else 'tests').resolve()
    try:
        return subprocess.run([sys.executable, '-m', 'pytest', '-q', str(tests)], env=env).returncode
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    sys.exit(main())
