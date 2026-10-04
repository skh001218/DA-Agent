"""Request-v2 contract and isolated real-DB workflow regressions.

Run DB cases with scripts/verify_request_training.py so user records and
capability approvals remain untouched. Fixture provider verifies contracts,
not semantic quality of an actual model.
"""
import copy
import json
import os
from pathlib import Path
import uuid

import pytest
from pydantic import ValidationError
from da_agent.task_contracts import RequestV2, RequestAction, PublicTaskV2
from da_agent.task_planner import parse_interpretation, assemble_plan, choose_scenario, signature
from da_agent.capabilities import CAPABILITIES
from da_agent.training_contracts import TrainingRequest, PublicTask
from da_agent.errors import DomainError


def request_v2(**changes):
    return RequestV2(contract_version='request-v2', request_id=str(uuid.uuid4()), message='접속 분석 연습', data_mode='existing', **changes)


def interpretation(kind='calculation', level='advanced', questions=None):
    return {'capability_id': 'access-' + kind, 'difficulty': level, 'task_kind': kind, 'goal': '접속 분석', 'questions': questions or [], 'unsupported': False, 'reason': '명시한 선택을 적용'}


def completed(value):
    return {'state': 'completed', 'model': 'fixture-v2', 'text': json.dumps(value, ensure_ascii=False)}


def test_v1_v2_separate_contracts_and_bounds():
    assert TrainingRequest(request_id='old', message='접속 분석').task_kind == 'auto'
    assert request_v2(task_kind='investigation').contract_version == 'request-v2'
    with pytest.raises(ValidationError):
        TrainingRequest(request_id='old', message='접속 분석', task_kind='investigation')
    for count in (49, 1001):
        with pytest.raises(ValidationError):
            request_v2(user_count=count)
    with pytest.raises(ValidationError):
        RequestV2.model_validate(dict(request_v2().model_dump(), sql='SELECT 1'))
    with pytest.raises(ValidationError):
        RequestAction(action_id='action', expected_revision=-1)


def test_planner_selection_conflict_and_invalid_capability():
    data = request_v2(task_kind='design', difficulty='beginner')
    selected = parse_interpretation(completed(interpretation()), data)
    assert len(selected.questions) == 2
    with pytest.raises(DomainError) as exc:
        parse_interpretation(completed(dict(interpretation(), capability_id='invented')), data)
    assert exc.value.code == 'plan_invalid'
    with pytest.raises(DomainError):
        parse_interpretation({'state': 'failed'}, data)
    assert parse_interpretation(completed(interpretation()), RequestV2.model_validate(dict(data.model_dump(), domain='payments'))).unsupported


def test_plan_four_kinds_preserves_public_private_boundary(tmp_path):
    from da_agent.data import generate_package
    from da_agent.evaluation import freeze_evaluation
    package = generate_package(tmp_path)
    for cap in CAPABILITIES:
        selection = interpretation(cap['task_kind'])
        public, private = assemble_plan(package, selection, 'fixed-plan', 2, cap['scenarios'][0], False)
        assert public['task_kind'] == cap['task_kind'] and public['revision'] == 2
        assert public['contract_version'] == 'request-v2'
        assert public['plan_id'] == 'fixed-plan' and 'cohort_start' not in public
        assert private['question_facts']['cohort_start']
        assert sum(public['weights'].values()) == 100
        assert 'expected' not in public and 'sql' not in public and 'seed' not in public
        assert freeze_evaluation(public, private)['contract_hash']
        if cap['task_kind'] == 'design':
            assert 'sql_accuracy' not in public['weights']
        with pytest.raises(ValidationError):
            PublicTaskV2.model_validate(dict(public, seed=1))
        with pytest.raises(ValidationError):
            PublicTask.model_validate(public)


def test_recent_semantic_signature_avoids_exact_repeat():
    selection = interpretation('investigation')
    first = choose_scenario(selection, [])
    second = choose_scenario(selection, [signature(selection, first)])
    assert first != second


class StructuredProvider:
    def __init__(self):
        self.calls = 0
        self.clarify_once = False
        self.contexts = []
    def status(self):
        return {'state': 'ready', 'provider': 'fixture', 'model': 'fixture-v2'}
    def review(self, messages):
        self.calls += 1
        payload = json.loads(messages[1]['content'])
        if 'request' in payload:
            data = payload['request']
            questions = []
            if self.clarify_once:
                self.clarify_once = False
                questions = ['대상 목표를 확인해주세요.']
            return completed(interpretation(data['task_kind'] if data['task_kind'] != 'auto' else 'calculation', data['difficulty'] if data['difficulty'] != 'auto' else 'advanced', questions))
        if payload.get('contract_version') == 'coaching-v2':
            self.contexts.append(copy.deepcopy(payload))
            return completed({'action_type': 'check', 'reason': '공개 조건을 확인', 'next_action': '관측 조건을 확인하세요.', 'evidence_ids': ['public-task'], 'evidence_state': 'public', 'uncertainty': ''})
        if 'weights' in payload:
            return completed({'criteria': [{'key': key, 'level': 2 if key == 'sql_accuracy' else 3, 'reason': '공개 완료 조건 검토', 'claim_ids': [], 'saved_execution_ids': []} for key in payload['weights']], 'strengths': [], 'improvements': [], 'next_steps': ['조건을 정리하세요.'], 'uncertainty': '실행 근거 없는 계산은 미확인'})
        return {'state': 'completed', 'model': 'fixture-v2', 'text': '관측 조건을 확인하세요.'}


@pytest.fixture
def v2_db_client(tmp_path):
    if os.getenv('RUN_DB_TESTS') != '1':
        pytest.skip('Real PostgreSQL requires RUN_DB_TESTS=1')
    from psycopg.conninfo import conninfo_to_dict
    options = conninfo_to_dict(os.environ.get('RECORDS_DSN', '')).get('options', '')
    if 'search_path=verify_request_' not in options:
        pytest.skip('Use verify_request_training.py disposable recorder schema')
    import shutil
    from fastapi.testclient import TestClient
    from da_agent.app import create_app
    from da_agent.config import Settings
    source = Path(os.environ.get('PACKAGES_ROOT', 'D:/Codex/DA-Agent/packages')).resolve()
    root = tmp_path / 'packages'
    shutil.copytree(source, root)
    settings = Settings(packages_root=root)
    settings.records_dsn = os.environ['RECORDS_DSN']
    if os.getenv('LEARNER_DSN'):
        settings.learner_dsn = os.environ['LEARNER_DSN']
    provider = StructuredProvider()
    with TestClient(create_app(settings, provider)) as client:
        yield client, provider


def begin_v2(client, kind='calculation', **changes):
    body = request_v2(task_kind=kind, difficulty='advanced').model_dump()
    body.update(changes)
    response = client.post('/api/training/requests', json=body)
    assert response.status_code == 200, response.text
    value = client.get('/api/training/requests/' + body['request_id']).json()
    return body, value


def test_db_all_four_ready_resume_idempotency_and_v1(v2_db_client):
    client, provider = v2_db_client
    for kind in ('calculation', 'review', 'design', 'investigation'):
        body, result = begin_v2(client, kind)
        assert result['status'] == 'ready', result
        assert not {'body', 'seed', 'fixed', 'interpretation'} & result.keys()
        attempt = client.get('/api/attempts/' + result['attempt_id']).json()
        assert attempt['problem']['task_kind'] == kind
        assert attempt['problem']['contract_version'] == 'request-v2'
        assert attempt['plan_hash']
        calls = provider.calls
        assert client.post('/api/training/requests', json=body).json()['attempt_id'] == result['attempt_id']
        assert provider.calls == calls
        assert client.post('/api/training/requests', json=dict(body, message='다른 목표')).status_code == 409
        assert client.get('/api/attempts/' + result['attempt_id']).json()['plan_hash'] == attempt['plan_hash']
    old = {'request_id': str(uuid.uuid4()), 'message': '접속 분석 연습', 'task_kind': 'design', 'difficulty': 'advanced'}
    assert client.post('/api/training/requests', json=old).status_code == 200
    value = client.get('/api/training/requests/' + old['request_id']).json()
    assert value['status'] == 'ready'
    assert client.get('/api/attempts/' + value['attempt_id']).json()['problem']['contract_version'] == 'request-v1'


def test_db_clarification_revision_and_action_conflict(v2_db_client):
    client, provider = v2_db_client
    provider.clarify_once = True
    body, result = begin_v2(client, 'design')
    assert result['status'] == 'needs_clarification' and result['attempt_id'] is None
    path = '/api/training/requests/' + body['request_id'] + '/clarify'
    action = {'action_id': 'clarify-1', 'expected_revision': result['revision'], 'message': '업무 목표를 구체화하겠습니다.'}
    assert client.post(path, json=dict(action, expected_revision=99)).status_code == 409
    assert client.post(path, json=action).status_code == 200
    ready = client.get('/api/training/requests/' + body['request_id']).json()
    assert ready['status'] == 'ready' and ready['revision'] == 1
    calls = provider.calls
    assert client.post(path, json=action).status_code == 200
    assert provider.calls == calls
    assert client.post(path, json=dict(action, message='다른 답변')).status_code == 409


def test_db_v2_coaching_noncollection_and_design_review(v2_db_client):
    client, provider = v2_db_client
    _, result = begin_v2(client, 'design')
    prefix = '/api/attempts/' + result['attempt_id']
    marker = 'V2_UNSAVED_' + uuid.uuid4().hex
    execution = client.post(prefix + '/execute', json={'sql': "SELECT '" + marker + "' AS marker"}).json()
    action = {'action_id': str(uuid.uuid4()), 'message': '이 결과를 확인해주세요.', 'execution_id': execution['execution_id']}
    coaching = client.post(prefix + '/conversation', json=action).json()
    assert coaching['status'] == 'completed' and coaching['transient']
    assert coaching['feedback']['action_type'] == 'check'
    calls = provider.calls
    assert client.post(prefix + '/conversation', json=action).json() == coaching
    assert provider.calls == calls
    assert not client.get(prefix).json()['messages']
    with client.app.state.store.connect() as conn:
        for table in ('training_messages', 'training_events', 'training_requests', 'training_plans', 'quality_operations'):
            assert marker not in json.dumps(conn.execute(f'SELECT row_to_json(t) AS row FROM {table} t').fetchall())
    payload = {'revision': 0, 'request_id': str(uuid.uuid4()), 'content': {'problem_definition': '대상과 관측 정의'}, 'claims': [{'claim_id': 'claim', 'text': '비교 가능한 관측 기간을 결정', 'evidence_refs': []}]}
    report = client.post(prefix + '/reports', json=payload).json()
    review = client.post(prefix + '/reports/' + report['report_id'] + '/review', json={'request_id': str(uuid.uuid4())}).json()
    assert review['status'] == 'completed', review
    assert review['rules_version'] == 'request-review-v2'
    assert not any(c['key'] == 'sql_accuracy' for c in review['feedback']['criteria'])


def test_db_unapproved_generation_and_validation_failure_publish_nothing(v2_db_client):
    client, _ = v2_db_client
    _, blocked = begin_v2(client, data_mode='generated')
    assert blocked['status'] == 'failed' and blocked['error_code'] == 'capability_unapproved'
    assert blocked['attempt_id'] is None
    runner = client.app.state.runner
    original = runner.execute
    try:
        runner.execute = lambda *args: {'execution_id': str(uuid.uuid4()), 'status': 'error'}
        _, failed = begin_v2(client)
        assert failed['status'] == 'failed' and failed['attempt_id'] is None
        assert failed['error_code'] == 'validation_failed'
    finally:
        runner.execute = original
    with client.app.state.store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM attempts WHERE payload->>'request_id'=%s", (failed['request_id'],)).fetchone()


def test_db_generated_stage_validates_without_approval_or_publication(tmp_path):
    if os.getenv('RUN_DB_TESTS') != '1' or not os.getenv('GENERATOR_DSN'):
        pytest.skip('Requires dedicated generator account')
    import hashlib
    import psycopg
    from psycopg import sql
    from da_agent.package_validation import stage, generator_dsn
    from da_agent.packages import read_json
    from da_agent.evaluation import verify_evidence
    package_id = 'test-stage-' + uuid.uuid4().hex
    schema_name = 'pkg_' + hashlib.sha256((package_id + '/v1').encode()).hexdigest()[:20]
    assert package_id.startswith('test-stage-') and schema_name.startswith('pkg_')
    try:
        package = stage(tmp_path, package_id, 321, 50, 'baseline')
        assert package.schema_name == schema_name
        assert read_json(package.path / 'private/validation.json')['status'] == 'validated'
        with psycopg.connect(generator_dsn()) as conn:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema_name)))
            cursor = conn.execute(package.reference('problem-001')['sql'])
            columns = [{'name': c.name} for c in cursor.description]
            rows = [list(row) for row in cursor.fetchall()]
        proof = {'saved_execution_id': 'operator-fixture', 'result': {'status': 'success', 'result_complete': True, 'columns': columns, 'rows': rows}}
        assert verify_evidence(proof, package.reference('problem-001')['expected'])['status'] == 'verified'
    finally:
        with psycopg.connect(generator_dsn()) as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema_name)))
