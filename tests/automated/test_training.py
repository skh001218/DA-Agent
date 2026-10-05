"""Public/private contract, real DB lifecycle and noncollection regressions."""
import json
import os
import uuid

import pytest
from pydantic import ValidationError

from da_agent.training import interpret, contains_material, build_plan, DESIGN_WEIGHTS
from da_agent.training_contracts import TrainingRequest, Conversation, OperatorEvent, PublicTask
from da_agent.reviews import normalize_review, review_report


def request(**kw):
    return TrainingRequest(request_id=str(uuid.uuid4()), message='접속 분석 훈련', **kw)


def test_supported_scope_and_conflicting_choices():
    plan, error = interpret(request(), [])
    assert plan['task_kind'] == 'calculation' and plan['difficulty'] == 'intermediate' and error is None
    for text in ['매출 분석', 'D7 리텐션 문제', 'SELECT * FROM users', '초급 고급 문제']:
        assert interpret(TrainingRequest(request_id='x', message=text), [])[0] is None
    assert interpret(TrainingRequest(request_id='x', message='초급 계산 연습', difficulty='advanced'), [])[0] is None
    assert interpret(request(), [{'task_kind': 'calculation'}])[0]['task_kind'] != 'calculation'


def test_contracts_reject_material_and_unsupported_fields():
    assert contains_material('SELECT * FROM users')
    assert contains_material('user_id | 결과')
    assert not contains_material('관측이 끝난 유저만 세면 될까요?')
    with pytest.raises(ValidationError):
        OperatorEvent(event_id='e', event_type='ai_started', occurred_at='2026-10-04T00:00:00Z', operation_id='o', status='running', sql='SELECT 1')
    with pytest.raises(ValidationError):
        Conversation(action_id='a', message='질문', trigger='typing')


def test_design_review_does_not_require_sql_criterion():
    data = {'criteria': [{'key': k, 'level': 3, 'reason': '근거', 'claim_ids': [], 'saved_execution_ids': []} for k in DESIGN_WEIGHTS], 'strengths': [], 'improvements': [], 'next_steps': []}
    result = normalize_review({'state': 'completed', 'text': json.dumps(data)}, {'claims': []}, [], DESIGN_WEIGHTS)
    assert result['status'] == 'completed' and result['feedback']['total_score'] == 75
    assert 'sql_accuracy' not in [x['key'] for x in result['feedback']['criteria']]


def test_public_plan_blocks_secret_fields_and_invalid_weights(tmp_path):
    from da_agent.data import generate_package
    package = generate_package(tmp_path)
    public, _ = build_plan(package, {'task_kind': 'design', 'difficulty': 'advanced', 'selection_reason': '검증용'})
    with pytest.raises(ValidationError):
        PublicTask.model_validate(dict(public, expected={'eligible_count': 165}))
    with pytest.raises(ValidationError):
        PublicTask.model_validate(dict(public, weights={'problem_definition': 10}))


def test_unknown_private_answer_is_blocked_in_review():
    class Package:
        public = {}
        def problem(self, _):
            return {'task_kind': 'calculation'}
        def reference(self, _):
            return {'weights': {'problem_definition': 100}, 'rubric': '공개 기준만 평가', 'expected': {'eligible_count': 165}}
    class Provider:
        def review(self, _):
            return {'state': 'completed', 'text': json.dumps({'criteria': [{'key': 'problem_definition', 'level': 3, 'reason': '기준 대상자는 165명입니다.', 'claim_ids': [], 'saved_execution_ids': []}], 'strengths': [], 'improvements': [], 'next_steps': []})}
    result = review_report(Provider(), Package(), {'problem_id': 'p', 'claims': []}, [])
    assert result['status'] == 'failed' and result['error']['code'] == 'answer_exposure'


class FixtureProvider:
    def __init__(self):
        self.calls = 0

    def status(self):
        return {'state': 'ready', 'provider': 'test'}

    def review(self, messages):
        self.calls += 1
        payload = json.loads(messages[1]['content'])
        if 'weights' in payload:
            return {'state': 'completed', 'model': 'fixture', 'text': json.dumps({'criteria': [{'key': k, 'level': 3, 'reason': '과제 기준에 맞는 접근', 'claim_ids': [], 'saved_execution_ids': []} for k in payload['weights']], 'strengths': [], 'improvements': [], 'next_steps': ['관측 한계를 정리하세요.']})}
        assert 'expected' not in payload and 'question_facts' not in payload
        return {'state': 'completed', 'model': 'fixture', 'text': '관측 가능한 기간을 먼저 확인하세요.', 'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}


@pytest.fixture
def db_client():
    if os.getenv('RUN_DB_TESTS') != '1':
        pytest.skip('Requires Compose PostgreSQL')
    from fastapi.testclient import TestClient
    from da_agent.app import create_app
    app = create_app(auth=FixtureProvider())
    with TestClient(app) as client:
        created = []
        yield client, created
        for attempt in created:
            client.request('DELETE', '/api/attempts/' + attempt, headers={'Content-Type': 'application/json'})


def start(client, created, kind='calculation', level='advanced'):
    body = {'request_id': str(uuid.uuid4()), 'message': '접속 분석 연습', 'task_kind': kind, 'difficulty': level}
    assert client.post('/api/training/requests', json=body).status_code == 200
    result = client.get('/api/training/requests/' + body['request_id']).json()
    assert result['status'] == 'ready', result
    created.append(result['attempt_id'])
    return result['attempt_id'], body


def test_real_request_validation_pinned_plan_and_idempotency(db_client):
    client, created = db_client
    for kind in ('calculation', 'review', 'design'):
        attempt, body = start(client, created, kind)
        resumed = client.get('/api/attempts/' + attempt).json()
        public = resumed['problem']
        assert public['task_kind'] == kind
        assert 'expected' not in public and 'cohort_start' not in public
        assert public['weights'] == (DESIGN_WEIGHTS if kind == 'design' else public['weights'])
        assert client.get('/api/attempts/' + attempt + '/business-facts').json()['cohort_start']
        assert client.post('/api/training/requests', json=body).json()['attempt_id'] == attempt
        assert client.post('/api/training/requests', json=dict(body, message='다른 요청')).status_code == 409
        assert client.get('/api/attempts/' + attempt).json()['plan_hash'] == resumed['plan_hash']


def test_temporary_coaching_saved_history_and_cross_training(db_client):
    client, created = db_client
    a, _ = start(client, created)
    b, _ = start(client, created)
    prefix = '/api/attempts/' + a
    marker = 'UNSAVED_REQUEST_COACH_' + uuid.uuid4().hex
    execution = client.post(prefix + '/execute', json={'sql': f"SELECT '{marker}' AS marker"}).json()
    data = {'action_id': str(uuid.uuid4()), 'message': '이 결과를 확인해주세요.', 'execution_id': execution['execution_id']}
    first = client.post(prefix + '/conversation', json=data).json()
    assert first['status'] == 'completed' and first['transient']
    calls = client.app.state.auth.calls
    assert client.post(prefix + '/conversation', json=data).json() == first
    assert client.app.state.auth.calls == calls
    assert client.post('/api/attempts/' + b + '/conversation', json=dict(data, action_id='cross')).status_code == 410
    assert not client.get(prefix).json()['messages']
    plain = {'action_id': str(uuid.uuid4()), 'message': '관측이 끝난 유저만 세면 될까요?'}
    assert not client.post(prefix + '/conversation', json=plain).json()['transient']
    assert len(client.get(prefix).json()['messages']) == 1
    pasted = {'action_id': str(uuid.uuid4()), 'message': f"SELECT '{marker}'"}
    assert client.post(prefix + '/conversation', json=pasted).json()['transient']
    with client.app.state.store.connect() as conn:
        for table in ('training_messages', 'training_events', 'training_requests', 'training_plans'):
            assert marker not in json.dumps(conn.execute(f'SELECT row_to_json(t) AS r FROM {table} t').fetchall())
    exported = client.get('/api/training/metrics/export')
    assert exported.status_code == 200 and 'attachment' in exported.headers['Content-Disposition']
    assert marker not in exported.text and exported.json()['definition_version'] == 'first-use-metrics-v1'


def test_design_submit_review_resume_and_delete(db_client):
    client, created = db_client
    attempt, body = start(client, created, 'design')
    prefix = '/api/attempts/' + attempt
    payload = {'revision': 0, 'request_id': str(uuid.uuid4()), 'content': {'problem_definition': '대상과 관측을 정의'}, 'claims': [{'claim_id': 'c1', 'text': '비교 가능한 관측기간을 결정', 'evidence_refs': []}]}
    report = client.post(prefix + '/reports', json=payload).json()
    result = client.post(prefix + '/reports/' + report['report_id'] + '/review', json={'request_id': str(uuid.uuid4())}).json()
    assert result['status'] == 'completed' and result['rules_version'] == 'request-review-v1'
    assert not any(x['key'] == 'sql_accuracy' for x in result['feedback']['criteria'])
    assert len(client.get(prefix).json()['reports']) == 1
    assert client.request('DELETE', prefix, headers={'Content-Type': 'application/json'}).json()['deleted']
    assert client.get(prefix).status_code == 404
    assert client.get('/api/training/requests/' + body['request_id']).status_code == 404
    assert attempt not in json.dumps(client.get('/api/training/metrics').json())
    created.remove(attempt)


def test_failed_validation_and_restart_never_publish(db_client):
    client, created = db_client
    from fastapi import BackgroundTasks
    engine = client.app.state.training
    body = request(task_kind='calculation')
    tasks = BackgroundTasks()
    engine.begin(body, tasks)
    engine.state(body.request_id, 'planning')
    engine.initialize()
    assert engine.request(body.request_id)['status'] == 'interrupted'
    original = engine.runner.execute
    try:
        engine.runner.execute = lambda *args: {'execution_id': 'failed', 'status': 'error'}
        body2 = request(task_kind='calculation')
        engine.begin(body2, BackgroundTasks())
        engine.prepare(body2.request_id)
        result = engine.request(body2.request_id)
        assert result['status'] == 'failed' and result['attempt_id'] is None
    finally:
        engine.runner.execute = original
        with engine.store.connect() as conn:
            for rid in (body.request_id, body2.request_id):
                conn.execute("DELETE FROM training_events WHERE payload->>'request_id'=%s", (rid,))
                conn.execute('DELETE FROM training_requests WHERE request_id=%s', (rid,))


def test_cancelled_request_cannot_publish(db_client):
    client, _ = db_client
    from fastapi import BackgroundTasks
    engine = client.app.state.training
    data = request()
    engine.begin(data, BackgroundTasks())
    response = client.post('/api/training/requests/' + data.request_id + '/cancel', json={})
    assert response.json()['status'] == 'cancelled'
    engine.prepare(data.request_id)
    assert engine.request(data.request_id)['attempt_id'] is None
    with engine.store.connect() as conn:
        conn.execute("DELETE FROM training_events WHERE payload->>'request_id'=%s", (data.request_id,))
        conn.execute('DELETE FROM training_requests WHERE request_id=%s', (data.request_id,))
