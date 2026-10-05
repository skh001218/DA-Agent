import datetime as dt
import json
import os
import uuid
import pytest
from pydantic import ValidationError
from da_agent import assessments, learning_state, metrics, telemetry
from da_agent.errors import DomainError


def event(**changes):
    return dict(event_id='event', event_type='sql_finished', operation_id='operation',
                occurred_at='2026-10-04T00:00:00Z', status='success', **changes)


@pytest.mark.parametrize('field,value', [('sql', 'SELECT secret'), ('rows', [{'secret': 123}]),
                                        ('message', 'private'), ('prompt', 'private'), ('response', 'private'), ('api_key', 'private')])
def test_telemetry_forbids_raw_material(field, value):
    with pytest.raises(ValidationError):
        telemetry.EventV2.model_validate(event(**{field: value}))


def test_telemetry_types_event_specific_fields_and_utc():
    for changes in ({'row_count': '3'}, {'occurred_at': '2026-10-04T09:00:00+09:00'},
                    {'event_type': 'arbitrary'}, {'error_code': 'SELECT secret'},
                    {'event_type': 'coaching_finished', 'row_count': 2}, {'input_tokens': 2}):
        data = event()
        data.update(changes)
        with pytest.raises(ValidationError):
            telemetry.EventV2.model_validate(data)
    assert telemetry.EventV2.model_validate(event(row_count=3, complete=False)).row_count == 3


def test_interpretation_diagnostic_closed_vocabulary_and_failure_scope():
    detail={'stage':'schema','issues':[{'field':'goal','kind':'length'}]}
    data=event()
    data.update(event_type='ai_finished',status='failed',error_code='format_invalid',failure_detail=detail)
    assert telemetry.EventV2.model_validate(data).failure_detail.stage=='schema'
    for bad in ({'stage':'raw private'}, {'stage':'schema','issues':[{'field':'raw private','kind':'length'}]}, {'stage':'schema','message':'SELECT SECRET'}):
        with pytest.raises(ValidationError):telemetry.EventV2.model_validate(dict(data,failure_detail=bad))
    with pytest.raises(ValidationError):telemetry.EventV2.model_validate(dict(data,status='completed',error_code=None))


def test_provisional_observation_never_copies_feedback_or_claims_mastery():
    review = {'review_id': 'r', 'report_id': 'report', 'attempt_id': 'a', 'status': 'completed',
              'rules_version': 'rv2', 'feedback': {'criteria': [{'key': 'interpretation', 'score': 20, 'reason': 'SECRET SELECT 123'}]}}
    value = learning_state.review_observations(review)
    assert len(value) == 1 and value[0]['competency'] == 'evidence_interpretation'
    assert value[0]['confirmed'] is False and value[0]['certainty'] == 'provisional'
    assert 'SECRET' not in json.dumps(value) and 'score' not in value[0]
    assert learning_state.review_observations(dict(review, status='failed')) == []


def test_observation_level_and_explicit_versioned_human_pass():
    review = {'review_id': 'r', 'report_id': 'report', 'attempt_id': 'a', 'status': 'completed', 'rules_version': 'rv2',
              'feedback': {'criteria': [{'key': 'interpretation', 'level': 1, 'score': 5, 'reason': 'SECRET'}]}}
    verdict = {'assessment_id': 'verdict', 'revision': 2, 'source': 'human', 'target_kind': 'review',
               'target_id': 'r', 'target_version': 'rv2', 'result': 'pass'}
    observation = learning_state.review_observations(review, verdict)[0]
    assert observation['level'] == 1 and observation['confirmed'] and observation['assessment_revision'] == 2
    assert not learning_state.review_observations(review, dict(verdict, target_version='old'))[0]['confirmed']
    assert not learning_state.review_observations(review, dict(verdict, source='learner'))[0]['confirmed']
    rejected = learning_state.review_observations(review, dict(verdict, result='misdiagnosis'))[0]
    assert not rejected['valid'] and not rejected['confirmed']
    assert 'SECRET' not in json.dumps(observation)


@pytest.mark.parametrize('changes', [{'status': 'unsupported'}, {'status': 'ready'}, {'status': 'success', 'error_code': 'timeout'},
                                   {'event_type': 'sql_started', 'status': 'running', 'row_count': 1},
                                   {'event_type': 'ai_started', 'status': 'running', 'input_tokens': 1}])
def test_telemetry_event_specific_states(changes):
    with pytest.raises(ValidationError):
        telemetry.EventV2.model_validate(dict(event(), **changes))


def test_assessment_source_pairs_and_repeat_contract():
    body = dict(target_kind='difficulty', target_id='a', target_version='v2', reviewer='operator', result='appropriate')
    assert assessments.AssessmentInput(**body).source == 'human'
    for update in ({'source': 'learner', 'result': 'ambiguous'}, {'sample_id': 's'},
                   {'target_kind': 'task_pair', 'result': 'meaningful_difference'}):
        with pytest.raises(ValidationError):
            assessments.AssessmentInput(**dict(body, **update))


def verdict(identifier, kind, result, revision=1, **extra):
    return dict(assessment_id=identifier, target_kind=kind, target_id=identifier, revision=revision,
                result=result, source='human', target_occurred_at='2026-10-04T00:00:00Z', **extra)


def test_eight_metrics_independent_denominators_latest_revision_and_missing():
    evidence = [verdict('pair', 'task_pair', 'no_difference'), verdict('pair', 'task_pair', 'meaningful_difference', 2),
                verdict('repeat', 'task_pair', 'intentional_repeat'), verdict('difficulty', 'difficulty', 'appropriate'),
                verdict('help', 'coaching', 'helpful'), verdict('bad', 'coaching', 'disruptive'),
                verdict('initial', 'report_pair', 'needs_improvement'),
                verdict('fixed', 'report_pair', 'improved')]
    evidence[-1]['target_id'] = 'initial'
    events = [{'event_id': 'g', 'event_type': 'generation_started', 'request_id': 'r', 'occurred_at': '2026-10-04T00:00:00Z'},
              {'event_id': 'g2', 'event_type': 'generation_started', 'request_id': 'r', 'occurred_at': '2026-10-04T01:00:00Z'}]
    value = metrics.aggregate(events=events, verdicts=evidence, requests=[{'request_id': 'r', 'status': 'ready'}, {'request_id': 'u', 'status': 'unsupported'}],
                              reviews=[{'status': 'completed'}, {'status': 'failed', 'error': {'code': 'review_format'}}],
                              pilots=[{'completed': True, 'assistance': 'no'}, {'completed': True, 'assistance': 'pending'}])
    assert value['generation_success']['denominator'] == 1 and value['generation_success']['unsupported'] == 1
    assert value['diversity']['rate'] == 1 and value['diversity']['intentional_repeat'] == 1
    assert value['difficulty_fit']['rate'] == 1
    assert value['coaching_appropriateness']['rate'] == .5
    assert value['evaluation_reliability']['rate'] == .5
    assert value['independent_performance']['rate'] == .5
    assert value['revision_effect']['rate'] == 1
    assert value['wait_usage']['duration']['p95_ms'] is None
    assert metrics.aggregate()['generation_success']['rate'] is None


def test_duration_nearest_rank_and_seoul_half_open_range():
    value = metrics.distribution(list(range(1, 21)) + [None])
    assert value == {'count': 20, 'missing': 1, 'median_ms': 10.5, 'p95_ms': 19}
    begin, end = metrics.korea_range('2026-10-04', '2026-10-04')
    assert begin.isoformat() == '2026-10-03T15:00:00+00:00'
    assert end.isoformat() == '2026-10-04T15:00:00+00:00'
    assert not metrics.matches({'created_at': end.isoformat()}, {'end_date': '2026-10-04'}, 'created_at')


def test_ai_missing_usage_and_legacy_no_invented_finish():
    starts = [{'event_id': 'a', 'event_type': 'ai_started', 'operation_id': 'a', 'occurred_at': '2026-10-04T00:00:00Z'},
              {'event_id': 'b', 'event_type': 'ai_started', 'operation_id': 'b', 'parent_operation_id': 'a', 'occurred_at': '2026-10-04T01:00:00Z'}]
    ends = [{'event_id': 'af', 'event_type': 'ai_finished', 'operation_id': 'a', 'input_tokens': 10, 'output_tokens': 20}]
    value = metrics.aggregate(events=starts + ends)
    assert value['wait_usage']['ai_attempts'] == 2 and value['wait_usage']['ai_retries'] == 1
    assert value['wait_usage']['groups']['unlinked'] == {'attempts': 2, 'retries': 1, 'observed_tokens': 30, 'missing_calls': 1}
    legacy = metrics.adapt_v1({'event_id': 'old', 'event_type': 'ai_started', 'status': 'completed', 'sql': 'SECRET'})
    assert 'rules_version' not in legacy and 'finished_at' not in legacy and 'sql' not in legacy
    assert metrics.aggregate(events=[legacy])['wait_usage']['ai_attempts'] == 1


def test_repeat_quality_run_adapter_drops_all_fixture_material():
    run = {'run_id': 'run', 'started_at': '2026-10-04T00:00:00Z', 'rules_version': 'review-v1',
           'samples': [{'id': 'sample', 'sql': 'SECRET SQL', 'evidence': ['SECRET ROW'], 'results': [
               {'repetition': 1, 'status': 'completed', 'feedback': {'total_score': 80, 'reason': 'SECRET REASON'}},
               {'repetition': 2, 'status': 'completed', 'feedback': {'total_score': 90}},
               {'repetition': 3, 'status': 'failed', 'error': {'code': 'provider_failure', 'message': 'SECRET ERROR'}}]}]}
    adapted = metrics.adapt_quality_run(run)
    assert 'SECRET' not in json.dumps(adapted)
    repeats = metrics.aggregate(reviews=adapted)['evaluation_reliability']['repeated_samples']
    assert repeats == [{'sample_id': 'run:sample', 'valid_count': 2, 'score_range': 10, 'failed_repetitions': 1}]


def test_legacy_string_error_is_safe_and_does_not_break_metrics():
    value = metrics.aggregate(reviews=[{'status': 'failed', 'error': 'SECRET provider response'}])
    assert value['evaluation_reliability']['errors'] == {'unknown': 1}
    assert 'SECRET' not in json.dumps(value)
    value = metrics.aggregate(reviews=[{'status': 'failed', 'error': {'code': 'SECRET sql'}}, {'status': 'failed', 'error': {'code': 'api_unavailable'}}])
    assert value['evaluation_reliability']['errors'] == {'unknown': 1, 'provider_failure': 1}
    assert 'SECRET' not in json.dumps(value)


def test_metrics_route_filters_exclude_closure_objects(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    monkeypatch.setattr(metrics, 'collect', lambda store, filters: {'filters': filters})
    metrics.routes(app, object())
    with TestClient(app) as client:
        result = client.get('/api/quality/metrics?difficulty=advanced')
        assert result.status_code == 200 and result.json() == {'filters': {'difficulty': 'advanced'}}
        result = client.get('/api/quality/metrics/export?format=json&difficulty=advanced')
        assert result.status_code == 200 and result.json() == {'filters': {'difficulty': 'advanced'}}


@pytest.mark.parametrize('value', ['=SUM(1)', '+cmd', '-2+3', '@evil', '  =cmd', '\tcmd', '\rcmd'])
def test_csv_formula_escaping(value):
    assert metrics.safe_csv(value).startswith("'")


@pytest.mark.skipif(os.getenv('RUN_DB_TESTS') != '1', reason='Requires PostgreSQL')
def test_real_db_event_idempotence_operation_restart_revisions_and_delete():
    from da_agent.config import Settings
    from da_agent.store import Store
    store = Store(Settings().records_dsn)
    store.initialize()
    telemetry.initialize(store)
    learning_state.initialize(store)
    assessments.initialize(store)
    token = str(uuid.uuid4())
    attempt = store.create({'package_id': 'test', 'release_version': 'v2', 'dataset_id': 'd', 'problem_id': 'p'})
    identifier = attempt['attempt_id']
    try:
        data = event()
        data.update(event_id=token, attempt_id=identifier)
        with store.connect() as conn:
            assert telemetry.append(conn, data) == telemetry.append(conn, data)
        with pytest.raises(DomainError) as exc:
            with store.connect() as conn:
                telemetry.append(conn, dict(data, row_count=2))
        assert exc.value.status == 409
        operation = telemetry.begin(store, 'ai', attempt_id=identifier, call_limit=1)
        with pytest.raises(DomainError) as exc:
            telemetry.begin(store, 'ai', attempt_id=identifier, call_limit=1)
        assert exc.value.status == 429
        telemetry.initialize(store)
        with store.connect() as conn:
            payload = conn.execute('SELECT payload FROM quality_operations WHERE operation_id=%s', (operation,)).fetchone()['payload']
            assert payload['status'] == 'interrupted' and payload['duration_ms'] is None
            ended = conn.execute("SELECT payload FROM quality_events_v2 WHERE payload->>'operation_id'=%s AND payload->>'event_type'='ai_finished'", (operation,)).fetchone()['payload']
            assert ended['status'] == 'interrupted' and ended['error_code'] == 'server_restart' and ended['usage_missing_reason'] == 'failed_call'
        with pytest.raises(DomainError):
            telemetry.finish(store, operation, 'success')
        assessment = assessments.create(store, assessments.AssessmentInput(target_kind='difficulty', target_id=identifier,
                                            target_version='v2', reviewer='test', result='appropriate'))
        changed = assessments.patch(store, assessment['assessment_id'], assessments.AssessmentPatch(expected_revision=1, result='ambiguous', reviewer='test'))
        assert changed['revision'] == 2 and changed['previous_revision'] == 1
        with pytest.raises(DomainError) as exc:
            assessments.patch(store, assessment['assessment_id'], assessments.AssessmentPatch(expected_revision=1, result='appropriate', reviewer='test'))
        assert exc.value.status == 409
        state = learning_state.get(store)
        updated = learning_state.patch(store, learning_state.StatePatch(expected_revision=state['state_revision'], preferences={'level': 'advanced', 'goal': 'goal'}))
        assert updated['state_revision'] == state['state_revision'] + 1
        with store.connect() as conn:
            assessments.delete_for_attempt(conn, identifier)
            learning_state.delete_for_attempt(conn, identifier)
            # No linked request exists; direct deletes exercise standalone modules.
            conn.execute("DELETE FROM quality_events_v2 WHERE payload->>'attempt_id'=%s", (identifier,))
            conn.execute("DELETE FROM quality_operations WHERE payload->>'attempt_id'=%s", (identifier,))
            assert not conn.execute('SELECT 1 FROM quality_assessments WHERE assessment_id=%s', (assessment['assessment_id'],)).fetchone()
    finally:
        with store.connect() as conn:
            conn.execute('DELETE FROM attempts WHERE attempt_id=%s', (identifier,))
