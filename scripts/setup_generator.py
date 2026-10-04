"""Provision least-privilege generator on existing DB without resetting data."""
import os
import secrets
from pathlib import Path
import psycopg
from psycopg import sql

def main():
    key=Path(os.getenv('GENERATOR_KEY_PATH','.local/generator.key'))
    key.parent.mkdir(parents=True,exist_ok=True)
    password=key.read_text().strip() if key.exists() else secrets.token_urlsafe(32)
    with psycopg.connect(os.environ['ADMIN_DSN']) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='generator'").fetchone():
            conn.execute(sql.SQL('CREATE ROLE generator LOGIN PASSWORD {}').format(sql.Literal(password)))
        else:
            conn.execute(sql.SQL('ALTER ROLE generator PASSWORD {}').format(sql.Literal(password)))
        conn.execute('GRANT CONNECT,CREATE,TEMPORARY ON DATABASE training TO generator')
    if not key.exists():
        key.write_text(password,encoding='utf-8')
    print('Generator role configured; existing records preserved.')

if __name__=='__main__':
    main()
