"""Request-v2 contract and isolated real-DB workflow regressions.

Run DB cases with scripts/verification/verify_request_training.py so user records and
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
    return {'analysis_topic': 'return_observation', 'capability_id': 'access-' + kind, 'difficulty': level, 'task_kind': kind, 'goal': '접속 분석', 'questions': questions or [], 'unsupported': False, 'reason': '명시한 선택을 적용'}


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


@pytest.mark.parametrize('payload,stage,field,kind', [
    ('not json SECRET_TEST', 'json', 'output', 'invalid_json'),
    (dict(interpretation(), difficulty='SECRET_TEST'), 'schema', 'difficulty', 'enum'),
    (dict(interpretation(), unsupported='false'), 'schema', 'unsupported', 'type'),
    (dict(interpretation(), goal='x'*201), 'schema', 'goal', 'length'),
    (dict(interpretation(), SECRET_TEST='raw private'), 'schema', 'unknown_field', 'extra'),
    (dict(interpretation(), capability_id='SECRET_TEST'), 'selection', 'capability_id', 'unknown_capability'),
    (dict(interpretation(), capability_id='access-design'), 'selection', 'capability_id', 'capability_kind_mismatch'),
])
def test_interpretation_diagnostics_are_specific_and_raw_free(payload, stage, field, kind):
    result = completed(payload) if isinstance(payload, dict) else {'state':'completed', 'text':payload}
    with pytest.raises(DomainError) as caught:
        parse_interpretation(result, request_v2())
    detail = caught.value.failure_detail
    assert detail['stage'] == stage
    assert {'field':field, 'kind':kind} in detail['issues']
    assert 'SECRET_TEST' not in json.dumps(detail) and 'raw private' not in json.dumps(detail)


def test_interpretation_missing_fields_and_provider_reasons():
    value = interpretation()
    del value['reason']
    with pytest.raises(DomainError) as caught:
        parse_interpretation(completed(value), request_v2())
    assert caught.value.failure_detail['issues'] == [{'field':'reason', 'kind':'missing'}]
    for reason, expected in [('api_rate_limited','api_rate_limited'), ('SECRET_TEST','unknown')]:
        with pytest.raises(DomainError) as caught:
            parse_interpretation({'state':'error', 'reason':reason}, request_v2())
        assert caught.value.failure_detail['stage'] == 'provider'
        assert caught.value.failure_detail['provider_reason'] == expected


class StructuredProvider:
    def research(self, messages):
        from test_case_research import research_result
        return research_result()
    def select_case(self, messages):
        from test_case_research import selection_result
        return selection_result()
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
            return completed({'action_type': 'check', 'reason': '공개 조건을 확인', 'next_action': '관측 조건을 확인하세요.', 'evidence_ids': ['public-task'], 'uncertainty': ''})
        if payload.get('evaluation_version') == 'request-review-v3':
            scopes = payload['criterion_inputs']
            return completed({'criteria':[{'key':r['key'],'conditions':[{'id':c['id'],'state':'met' if c['kind']=='error' else 'missing' if c['kind']=='advanced' or not any(scopes[r['key']].values()) else 'met','reason':'계약 검증용 고정 응답','sources':[] if c['kind'] in ('advanced','error') else [{'path':next(p for p,t in scopes[r['key']].items() if t)}] if any(scopes[r['key']].values()) else []} for c in r['conditions']]} for r in payload['rubric']['criteria']], 'strengths':[], 'improvements':[], 'next_steps':[], 'uncertainty':''})
        if 'weights' in payload:
            return completed({'criteria': [{'key': key, 'level': 2 if key == 'sql_accuracy' else 3, 'reason': '공개 완료 조건 검토', 'claim_ids': [], 'saved_execution_ids': []} for key in payload['weights']], 'strengths': [], 'improvements': [], 'next_steps': ['조건을 정리하세요.'], 'uncertainty': '실행 근거 없는 계산은 미확인'})
        return {'state': 'completed', 'model': 'fixture-v2', 'text': '관측 조건을 확인하세요.'}


@pytest.fixture
def v2_db_client(tmp_path, monkeypatch):
    monkeypatch.setenv('QUALITY_CALL_INTERVAL_SECONDS','0')
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


def test_interpretation_failure_logs_survive_retry_without_raw_material(v2_db_client):
    client, provider = v2_db_client
    original = provider.review
    provider.review = lambda messages: {'state':'completed', 'model':'fixture-v2', 'usage':{'input_tokens':11,'output_tokens':7}, 'text':json.dumps(dict(interpretation(), SECRET_TEST='private response', unsupported='private value'))}
    body, failed = begin_v2(client)
    rid = body['request_id']
    assert failed['status']=='failed' and failed['planning_calls']==1
    assert failed['failure_detail']['stage']=='schema'
    assert 'SECRET_TEST' not in json.dumps(failed['failure_detail'])
    logs = client.get('/api/quality/diagnostics',params={'request_id':rid}).json()['events']
    assert len(logs)==2
    assert all(e['error_code']=='format_invalid' for e in logs)
    assert all(e['prompt_version']=='bounded-planner-v3' for e in logs if e['event_type']=='ai_finished')
    assert 'private response' not in json.dumps(logs) and 'private value' not in json.dumps(logs) and 'SECRET_TEST' not in json.dumps(logs)
    with client.app.state.store.connect() as conn:
        ended=conn.execute("SELECT payload FROM quality_events_v2 WHERE payload->>'request_id'=%s AND payload->>'event_type'='ai_finished'",(rid,)).fetchone()['payload']
    assert ended['input_tokens']==11 and ended['output_tokens']==7
    provider.review = original
    retry = client.post('/api/training/requests/'+rid+'/retry',json={'action_id':str(uuid.uuid4()),'expected_revision':failed['revision']})
    assert retry.status_code==200
    ready = client.get('/api/training/requests/'+rid).json()
    assert ready['status']=='ready' and ready['failure_detail'] is None
    assert len(ready['failure_history'])==1 and ready['failure_history'][0]['failure_detail']['stage']=='schema'
    assert len(client.get('/api/quality/diagnostics',params={'request_id':rid}).json()['events'])==2
    assert client.get('/api/quality/diagnostics?limit=101').status_code==422


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
    with client.app.state.store.connect() as conn:
        ended = conn.execute("SELECT payload FROM quality_events_v2 WHERE payload->>'request_id'=%s AND payload->>'event_type'='generation_finished'",(body['request_id'],)).fetchone()['payload']
    assert ended['status']=='completed' and not ended.get('error_code') and not ended.get('failure_detail')
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
    assert review['rules_version'] == 'evaluation-rubric-v3'
    assert review['evaluation_version'] == 'request-review-v3'
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
    if os.getenv('RUN_DB_TESTS') != '1' or not (os.getenv('GENERATOR_DSN') or Path(os.getenv('GENERATOR_PASSWORD_FILE','/run/secrets/generator_password')).is_file()):
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


def test_db_generated_approval_failure_retry_same_data_and_ready(v2_db_client):
    """Synthetic human verdict only in the disposable recorder test schema."""
    client,_=v2_db_client
    if not (os.getenv('GENERATOR_DSN') or Path(os.getenv('GENERATOR_PASSWORD_FILE','/run/secrets/generator_password')).is_file()):
        pytest.skip('Requires dedicated generator account')
    import psycopg
    from psycopg import sql
    from da_agent.package_validation import generator_dsn
    from da_agent.packages import digest,read_json
    engine=client.app.state.training
    packages=[]
    original=engine.runner.execute
    try:
        samples=[]
        for level in ('beginner','intermediate','advanced'):
            response=client.post('/api/training/capabilities/access-calculation/samples',json={'task_kind':'calculation','difficulty':level,'user_count':50})
            assert response.status_code==200,response.text
            sample=response.json();samples.append(sample['sample_id']);packages.append(sample['package_id'])
        assert client.post('/api/training/capabilities/access-calculation/approval',json={'reviewer':'fixture-only-not-real-human','sample_ids':samples,'rules_version':'access-rules-v2','result':'approved'}).status_code==200
        engine.runner.execute=lambda *args,**kwargs:{'execution_id':str(uuid.uuid4()),'status':'error'}
        body,failed=begin_v2(client,data_mode='generated',user_count=50)
        assert failed['status']=='failed' and failed['attempt_id'] is None
        with engine.store.connect() as conn:
            fixed=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(body['request_id'],)).fetchone()['private']
        packages.append(fixed['package_id'])
        package=engine.catalog.load(fixed['package_id'],'v1',allow_unvalidated=True)
        before=digest(package.path/'public/users.csv')
        assert read_json(package.path/'private/validation.json')['status']=='validated'
        with psycopg.connect(generator_dsn()) as conn:
            assert not conn.execute("SELECT has_schema_privilege('learner',%s,'USAGE')",(package.schema_name,)).fetchone()[0]
        engine.runner.execute=original
        action={'action_id':'manual-retry','expected_revision':failed['revision']}
        response=client.post('/api/training/requests/'+body['request_id']+'/retry',json=action)
        assert response.status_code==200,response.text
        ready=client.get('/api/training/requests/'+body['request_id']).json()
        assert ready['status']=='ready' and ready['generation_attempts']==2
        assert digest(package.path/'public/users.csv')==before
        attempt=client.get('/api/attempts/'+ready['attempt_id']).json()
        assert attempt['problem']['revision']==0 and attempt['problem']['plan_id']==fixed['plan_id']
        assert client.post('/api/training/requests/'+body['request_id']+'/retry',json=action).json()['attempt_id']==ready['attempt_id']
        assert client.post('/api/attempts',json={'package_id':fixed['package_id'],'release_version':'v1','problem_id':'problem-001'}).status_code==409
        assert client.request('DELETE','/api/attempts/'+ready['attempt_id'],json={}).json()['deleted']
    finally:
        engine.runner.execute=original
        # These random identities were created only in this test's temporary root.
        for package_id in packages:
            assert package_id.startswith(('sample-','generated-'))
            package=engine.catalog.load(package_id,'v1',allow_unvalidated=True)
            with psycopg.connect(generator_dsn()) as conn:
                conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(package.schema_name)))


@pytest.mark.parametrize('changes', [
    {'message': '매출과 결제 데이터 분석을 연습하고 싶습니다'},
    {'message': '게임 비정상 이용자 탐지 및 현상 조사 문제를 내줘'},
    {'message': '접속 로그로 봇 이용자를 탐지하고 싶어'},
    {'goal': '비정상 이용자 탐지'},
])
def test_db_known_unsupported_scope_does_not_call_model(v2_db_client, changes):
    client,provider=v2_db_client
    before=provider.calls
    _,result=begin_v2(client,**changes)
    assert result["status"]=="failed" and result["error_code"]=="unsupported_scope"
    assert result["attempt_id"] is None and not result["retry_allowed"]
    assert provider.calls==before and result["planning_calls"]==0
    assert "접속 데이터" in result["error"]
