from datetime import timedelta
import pytest

from da_agent.data import START, COMPLETE, calculate, generate_package, iso


def user(signup=START):
    return {"user_id": "u1", "signup_at": iso(signup)}


@pytest.mark.parametrize("offset,churn", [(timedelta(hours=1), 1), (timedelta(days=1), 0), (timedelta(days=8, seconds=-1), 0), (timedelta(days=8), 1), (timedelta(days=3), 0)])
def test_login_boundaries(offset, churn):
    sessions = [{"user_id": "u1", "login_at": iso(START + offset)}]
    assert calculate([user()], sessions)["churned_count"] == churn


def test_repeat_sessions_count_user_once():
    sessions = [{"user_id": "u1", "login_at": iso(START + timedelta(days=day))} for day in [1, 1, 3, 7]]
    assert calculate([user()], sessions) == {"eligible_count": 1, "churned_count": 0, "excluded_incomplete_count": 0, "churn_rate": 0.0}


def test_d0_cross_midnight_does_not_return():
    signup = START + timedelta(hours=23)
    sessions = [{"user_id": "u1", "login_at": iso(signup), "logout_at": iso(signup + timedelta(hours=2))}]
    assert calculate([user(signup)], sessions)["churned_count"] == 1


def test_collection_boundary_and_empty_denominator():
    signup = COMPLETE - timedelta(days=8)
    assert calculate([user(signup)], [], complete=COMPLETE)["eligible_count"] == 1
    result = calculate([user(signup)], [], complete=COMPLETE - timedelta(seconds=1))
    assert result == {"eligible_count": 0, "churned_count": 0, "excluded_incomplete_count": 1, "churn_rate": None}


def test_cohort_start_included_end_excluded():
    result = calculate([user()], [], start=START, end=START + timedelta(seconds=1))
    assert result["eligible_count"] == 1
    assert calculate([user()], [], start=START - timedelta(days=1), end=START)["eligible_count"] == 0


def test_generated_fixtures(tmp_path):
    p = generate_package(tmp_path)
    users, sessions = p.rows("users"), p.rows("sessions")
    assert len(users) == 200
    # Fixture identities are private generation knowledge; ordinary APIs expose no flags.
    expected = {"u0001": 1, "u0002": 1, "u0003": 0, "u0004": 0, "u0005": 1, "u0006": 0, "u0007": 0, "u0008": 1, "u0009": 1}
    for uid, churn in expected.items():
        assert calculate([next(u for u in users if u["user_id"] == uid)], sessions)["churned_count"] == churn
    assert calculate([users[9]], sessions)["eligible_count"] == 0
    assert calculate([users[10]], sessions)["eligible_count"] == 1
    assert calculate([users[11]], sessions)["excluded_incomplete_count"] == 1
