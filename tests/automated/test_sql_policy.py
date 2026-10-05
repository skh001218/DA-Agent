"""Exercise real SQL AST policy, including the essential churn anti-join.

No PostgreSQL connection is required: DB identity restrictions are tested by
the integration suite separately.
"""
import datetime as dt
from decimal import Decimal

import pytest

from da_agent.errors import DomainError
from da_agent.sql_runner import check_query, encode


@pytest.mark.parametrize("query", [
    "SELECT COUNT(DISTINCT user_id) FROM users",
    "SELECT COUNT(*) FILTER (WHERE country = 'KR') FROM users",
    "SELECT DATE_TRUNC('day', signup_at AT TIME ZONE 'Asia/Seoul') FROM users",
    "SELECT CAST(signup_at AS date) + INTERVAL '8 days' FROM users",
    "WITH cohort AS (SELECT user_id FROM users) SELECT COUNT(*) FROM cohort",
    "SELECT u.user_id FROM users u WHERE NOT EXISTS "
    "(SELECT 1 FROM sessions s WHERE s.user_id = u.user_id)",
    "SELECT user_id FROM users WHERE user_id IN (SELECT user_id FROM sessions)",
    "SELECT COUNT(*) OVER () FROM users",
])
def test_ordinary_analytical_queries_allowed(query):
    check_query(query)


@pytest.mark.parametrize("query", [
    "SELECT * FROM pg_catalog.pg_authid",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM other_schema.users",
    "SELECT * FROM records.public.attempts",
    "SELECT pg_sleep(1)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT current_setting('data_directory')",
    "SELECT set_config('statement_timeout', '0', false)",
    "SELECT pg_catalog.count(*) FROM users",
    "SELECT 'users'::regclass",
    "SELECT * FROM users FOR UPDATE",
    "SELECT * INTO copied_users FROM users",
    "SELECT * FROM users; SELECT * FROM sessions",
    "WITH changed AS (DELETE FROM users RETURNING *) SELECT * FROM changed",
    "WITH changed AS (UPDATE users SET country='XX' RETURNING *) SELECT * FROM changed",
    "COPY users TO '/tmp/users.csv'",
    "DROP TABLE users",
])
def test_writes_and_private_or_external_access_blocked(query):
    with pytest.raises(DomainError) as failure:
        check_query(query)
    assert failure.value.code == "blocked"


def test_syntax_failure_does_not_echo_unsaved_query():
    query = "SELECT user_private_token_marker FROM WHERE"
    with pytest.raises(DomainError) as failure:
        check_query(query)
    assert failure.value.code == "syntax"
    assert "user_private_token_marker" not in failure.value.message


def test_lossless_json_values_and_utc_timestamp():
    timestamp = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    assert encode(timestamp) == "2026-09-01T00:00:00Z"
    assert encode(Decimal("0.12345678901234567890")) == "0.12345678901234567890"
    assert encode(9007199254740992) == "9007199254740992"
    assert encode(9007199254740991) == 9007199254740991
    assert encode([None, True, Decimal("10.5")]) == [None, True, "10.5"]
