"""Mocked provider and real isolated PostgreSQL semantic query checks."""
import copy
import json
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import psycopg
from psycopg import sql
import pytest

from da_agent.discord_query import DiscordQueryEngine, QueryContractError, compile_query
from da_agent.sql_runner import check_query, SqlRunner


TASK = {'schema': {'users': {'user_id': 'text', 'signup_date': 'date', 'channel': 'text'},
                  'tutorial_attempts': {'user_id': 'text', 'step': 'integer', 'completed': 'boolean', 'attempt_at': 'timestamptz'},
                  'sessions': {'user_id': 'text', 'session_at': 'timestamptz'}},
        'timezone': 'UTC', 'period': {'start': '2026-09-01', 'end': '2026-09-15', 'observation_end': '2026-09-30'},
        'private_generation_recipe': 'PRIVATE_NEVER_DISCLOSE', 'hidden_answer': 'PRIVATE_ANSWER'}
BASE = {'metric': 'users_count', 'start': '2026-09-01', 'end': '2026-09-15',
        'timezone': 'UTC', 'period_basis': 'explicit_dates', 'unit': 'user', 'filters': {}, 'group_by': None}


def conditions(metric='users_count', **overrides):
    c = dict(BASE, metric=metric)
    if metric in {'attempts_count', 'duplicate_attempts'}:
        c['unit'] = 'attempt'
    if metric in {'tutorial_rate', 'duplicate_attempts'}:
        c['step'] = 3
    if metric == 'tutorial_rate':
        c.update(numerator='completed_users', denominator='signup_users', event_start='2026-09-01', event_end='2026-09-15')
    if metric == 'd7_retention':
        c.update(numerator='d7_active_users', denominator='observed_signup_users')
    if metric == 'weekly_return':
        c.update(numerator='returning_users', denominator='baseline_active_users', baseline_start='2026-08-25', baseline_end='2026-09-01')
    return dict(c, **overrides)


class Provider:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.messages = payload, error, []
    def review(self, messages):
        self.messages = messages
        if self.error:
            return {'state': 'error', 'reason': self.error}
        return {'state': 'completed', 'text': json.dumps(self.payload), 'usage': {'input_tokens': 10, 'output_tokens': 20}}


class Runner:
    def __init__(self, results=None):
        self.calls = []
        self.results = results or [{'status': 'success', 'execution_id': 'exec-1', 'rows': [[1]], 'result_complete': True}]
    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]
    def get(self, session_id, execution_id):
        return {'sql': self.calls[-1][0][2], 'result': dict(self.results[-1], rows=[[1], [2]])}


def engine(provider, runner=None):
    return DiscordQueryEngine(provider, runner or Runner(), SimpleNamespace(preview_rows=200, max_rows=1000, max_bytes=1048576, query_timeout_ms=5000))


@pytest.mark.parametrize('metric', sorted({'users_count', 'attempts_count', 'duplicate_attempts', 'tutorial_rate', 'd7_retention', 'weekly_return'}))
def test_all_operators_sql_policy(metric):
    check_query(compile_query(conditions(metric), TASK), TASK['schema'])


def test_public_projection_excludes_hidden_fields_and_model_sql_is_ignored():
    provider = Provider({'state': 'ready', 'conditions': BASE, 'sql': 'DROP TABLE users'})
    result = engine(provider).resolve('과제 기간 가입자 수', TASK)
    assert result['state'] == 'ready'
    assert 'DROP' not in result['sql']
    assert 'PRIVATE' not in json.dumps(provider.messages)
    assert result['usage']['input_tokens'] == 10


def test_public_metric_definitions_are_provided_without_extra_private_fields():
    task = copy.deepcopy(TASK)
    task.update(objective='공개 업무 목적', accepted_limits=['인과 식별 불가'])
    task['metrics'] = {'tutorial_rate': {'numerator': '고유 완료 사용자', 'denominator': '가입 사용자',
                                        'step': 3, 'event_end': '2026-09-30', 'private_answer': 'PRIVATE_RESULT'}}
    task['dictionary'] = {'users': {'description': '공개 사전', 'unit': '사용자', 'private_recipe': 'PRIVATE_RECIPE'}}
    provider = Provider({'state': 'clarification', 'conditions': {'metric': 'tutorial_rate'}})
    result = engine(provider).resolve('조회', task)
    envelope = json.loads(provider.messages[-1]['content'])
    assert envelope['public_task']['metrics']['tutorial_rate']['denominator'] == '가입 사용자'
    assert envelope['public_task']['dictionary']['users']['description'] == '공개 사전'
    assert 'PRIVATE' not in json.dumps(envelope)
    assert result['proposed_conditions'] == {'metric': 'tutorial_rate'}


@pytest.mark.parametrize('override', [
    {'timezone': 'Europe/London'}, {'unit': 'event'}, {'group_by': 'hidden_cause'},
    {'filters': {'private': 'x'}}, {'metric': 'delete'}, {'start': 'bad'},
    {'end': '2026-09-01'}, {'period_basis': 'week'}, {'extra_sql': 'SELECT 1'},
])
def test_invalid_or_ambiguous_conditions_do_not_execute(override):
    runner = Runner()
    result = engine(Provider({'state': 'ready', 'conditions': dict(BASE, **override)}), runner).resolve('조회', TASK)
    assert result['state'] == 'clarification'
    assert not runner.calls


def test_rate_missing_denominator_requires_confirmation():
    c = conditions('tutorial_rate')
    del c['denominator']
    assert engine(Provider({'state': 'ready', 'conditions': c})).resolve('완료율', TASK)['state'] == 'clarification'


def test_missing_public_columns_fail_closed():
    task = copy.deepcopy(TASK)
    del task['schema']['sessions']['session_at']
    with pytest.raises(QueryContractError):
        compile_query(conditions('d7_retention'), task)


def test_beginner_choices_are_recorded_as_hint():
    provider = Provider({'state': 'clarification', 'question': 'PRIVATE_ANSWER'})
    beginner = engine(provider).resolve('비율', TASK, difficulty='beginner')
    advanced = engine(provider).resolve('비율', TASK, difficulty='advanced')
    assert beginner['help_type'] == 'concept_hint' and beginner['options']
    assert advanced['help_type'] == 'request_confirmation' and not advanced['options']
    assert 'PRIVATE' not in beginner['question']


def test_previous_conditions_only_reused_explicitly_and_original_answer_retained():
    c = {'group_by': 'channel'}
    provider = Provider({'state': 'ready', 'conditions': c, 'reuse_previous': True})
    result = engine(provider).resolve('같은 기준 채널별', TASK, previous_conditions=BASE, clarification='가입자 분모')
    assert result['state'] == 'ready' and result['conditions']['group_by'] == 'channel'
    assert result['original']['clarification'] == '가입자 분모'
    provider.payload['reuse_previous'] = False
    assert engine(provider).resolve('조회', TASK, previous_conditions=BASE)['state'] == 'clarification'


def test_api_failure_never_fabricates_result():
    runner = Runner()
    result = engine(Provider(error='api_key_missing'), runner).resolve('조회', TASK)
    assert result['state'] == 'error' and result['reason'] == 'api_key_missing'
    assert 'rows' not in result and not runner.calls


def test_success_captures_full_collected_result_immediately():
    runner = Runner()
    e = engine(Provider({'state': 'ready', 'conditions': BASE}), runner)
    plan = e.resolve('조회', TASK)
    result = e.execute('s1', 'public_test_schema', plan, TASK)
    assert result['state'] == 'success'
    assert len(result['result']['rows']) == 1 and len(result['full_result']['rows']) == 2
    assert result['attempts'][0]['sql'] == plan['sql']
    assert runner.calls[0][1]['allowed_tables'] == {'users'}
    runner.results[-1]['rows'].append(['later'])
    assert result['full_result']['rows'] == [[1], [2]]


def test_tampered_plan_does_not_execute():
    runner = Runner()
    e = engine(Provider({'state': 'ready', 'conditions': BASE}), runner)
    plan = e.resolve('조회', TASK)
    plan['sql'] = 'SELECT * FROM pg_authid'
    assert e.execute('s1', 'schema', plan, TASK)['state'] == 'error'
    assert not runner.calls


@pytest.mark.parametrize('code, expected_calls', [('connection', 2), ('40001', 2), ('blocked', 1), ('timeout', 1), ('syntax', 1)])
def test_bounded_same_intent_retry_preserves_every_attempt(code, expected_calls):
    failed = {'status': 'error', 'error': {'code': code, 'message': 'Actual runner error'}}
    runner = Runner([failed])
    e = engine(Provider({'state': 'ready', 'conditions': BASE}), runner)
    result = e.execute('s', 'schema', e.resolve('조회', TASK), TASK)
    assert result['state'] == 'error' and len(result['attempts']) == expected_calls
    assert all(a['error'] == failed['error'] for a in result['attempts'])
    assert len({a['sql'] for a in result['attempts']}) == 1


def test_result_capture_failure_cannot_be_used_as_evidence():
    runner = Runner()
    def expired(*args):
        raise ValueError('expired')
    runner.get = expired
    e = engine(Provider({'state': 'ready', 'conditions': BASE}), runner)
    result = e.execute('s', 'schema', e.resolve('조회', TASK), TASK)
    assert result['reason'] == 'result_capture_failed' and 'saved_execution' not in result


@pytest.fixture
def db():
    dsn = os.getenv('DISCORD_TEST_DSN')
    if not dsn:
        pytest.skip('Set DISCORD_TEST_DSN to a dedicated isolated PostgreSQL database')
    # Only explicitly named isolated DB is allowed; never default to production.
    if '/discord_test' not in dsn:
        raise AssertionError('DISCORD_TEST_DSN must name dedicated discord_test database')
    schema = 'discord_query_test_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            conn.execute('CREATE TABLE users(user_id text PRIMARY KEY, signup_date date, channel text)')
            conn.execute('CREATE TABLE tutorial_attempts(user_id text, step integer, completed boolean, attempt_at timestamptz)')
            conn.execute('CREATE TABLE sessions(user_id text, session_at timestamptz)')
            learner_dsn = os.getenv('DISCORD_TEST_LEARNER_DSN')
            if learner_dsn:
                assert '/discord_test' in learner_dsn
                from psycopg.conninfo import conninfo_to_dict
                learner_role = conninfo_to_dict(learner_dsn)['user']
                conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(learner_role)))
                conn.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(learner_role)))
            conn.execute("INSERT INTO users VALUES ('a','2026-09-01','ad'), ('b','2026-09-02','ad'), ('c','2026-09-14','organic'), ('d','2026-09-15','organic'), ('e','2026-09-23','ad')")
            conn.execute("INSERT INTO tutorial_attempts VALUES ('a',3,true,'2026-09-02 10:00Z'), ('a',3,true,'2026-09-03 10:00Z'), ('b',3,false,'2026-09-04 10:00Z'), ('c',3,true,'2026-09-15 00:00Z'), ('b',2,true,'2026-09-03 10:00Z')")
            conn.execute("INSERT INTO sessions VALUES ('a','2026-08-26 00:00Z'), ('a','2026-08-27 00:00Z'), ('b','2026-08-28 00:00Z'), ('a','2026-09-08 00:00Z'), ('a','2026-09-08 12:00Z'), ('b','2026-09-08 12:00Z'), ('c','2026-09-21 00:00Z'), ('d','2026-09-22 00:00Z'), ('e','2026-09-30 00:00Z')")
            yield conn, dsn, schema
        finally:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def query(db, c):
    return [tuple(float(value) if isinstance(value, Decimal) else value for value in row)
            for row in db[0].execute(compile_query(c, TASK)).fetchall()]


def test_real_db_dedup_cohort_denominator_and_half_open_boundary(db):
    assert query(db, conditions()) == [(3,)]
    assert query(db, conditions('tutorial_rate')) == [(3, 1, pytest.approx(100 / 3))]
    assert query(db, conditions('tutorial_rate', denominator='attempted_users')) == [(2, 1, pytest.approx(50))]
    assert query(db, conditions('duplicate_attempts')) == [(1,)]
    assert query(db, conditions('attempts_count')) == [(4,)]


def test_real_db_group_filter_and_zero_denominator(db):
    rows = query(db, conditions('tutorial_rate', group_by='channel'))
    assert rows == [('ad', 2, 1, pytest.approx(50)), ('organic', 1, 0, pytest.approx(0))]
    assert query(db, conditions('tutorial_rate', filters={'channel': 'absent'})) == [(0, 0, None)]
    assert query(db, conditions('users_count', group_by='channel', filters={'channel': 'absent'})) == []


def test_real_db_d7_is_exact_day_and_excludes_unobserved_cohort(db):
    assert query(db, conditions('d7_retention', end='2026-09-30')) == [(4, 3, pytest.approx(75))]
    # a twice on D7 counted once; b on D6 excluded; e D7 not fully observed.
    assert query(db, conditions('d7_retention', start='2026-09-23', end='2026-09-30')) == [(0, 0, None)]


def test_real_db_weekly_return_is_distinct_from_d7(db):
    assert query(db, conditions('weekly_return')) == [(2, 2, pytest.approx(100))]


def test_real_db_timezone_date_boundary(db):
    task = dict(TASK, timezone='Asia/Seoul')
    db[0].execute("INSERT INTO tutorial_attempts VALUES ('c',3,false,'2026-09-14 16:00Z')")
    # 16:00 UTC on Sep14 is Sep15 KST and excluded by end Sep15.
    assert db[0].execute(compile_query(conditions('attempts_count', timezone='Asia/Seoul'), task)).fetchall() == [(4,)]


def test_real_db_readonly_runner_complete_capture_and_limits(db):
    settings = SimpleNamespace(learner_dsn=os.getenv('DISCORD_TEST_LEARNER_DSN', db[1]), preview_rows=1, max_rows=1000, max_bytes=1048576,
                               query_timeout_ms=5000, execution_ttl=600)
    e = DiscordQueryEngine(Provider({'state': 'ready', 'conditions': conditions(group_by='channel')}), SqlRunner(settings), settings)
    result = e.execute('isolated-session', db[2], e.resolve('채널별 가입자', TASK), TASK)
    assert result['state'] == 'success'
    assert result['result']['truncated'] and len(result['result']['rows']) == 1
    assert result['full_result']['result_complete'] and len(result['full_result']['rows']) == 2
    assert not result['full_result']['truncated']
