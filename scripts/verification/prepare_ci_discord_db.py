"""Initialize only the disposable databases named by the CI workflow."""
import os
import psycopg


def main():
    dsn = os.environ['DISCORD_TEST_ADMIN_DSN']
    if not dsn.endswith('/discord_ci_training'):
        raise SystemExit('This helper requires the discord_ci_training database name')
    base = dsn.rsplit('/', 1)[0] + '/postgres'
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute('CREATE DATABASE discord_ci_training')
        conn.execute('CREATE DATABASE discord_ci_records')
        conn.execute("CREATE ROLE ci_reader LOGIN PASSWORD 'ci-reader-password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
    print('Disposable CI databases prepared')


if __name__ == '__main__':
    main()
