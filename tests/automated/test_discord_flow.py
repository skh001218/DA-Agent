"""Real isolated PostgreSQL end-to-end lifecycle with scripted Gemini responses."""
import json
import os
from types import SimpleNamespace
import uuid

import pytest

from da_agent.discord_store import DiscordStore
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_query import DiscordQueryEngine
from da_agent.sql_runner import SqlRunner


class ScriptedProvider:
    def __init__(self):
        self.calls = 0
        self.contexts = []
        self.fail = False

    def review(self, messages):
        self.calls += 1
        context = json.loads(messages[-1]['content'])
        self.contexts.append(context)
        if self.fail:
            return {'state': 'error', 'reason': 'api_rate_limited'}
        if context.get('contract_version') == 'discord-query-v1':
            if not context.get('clarification') and context['request'] == '비율을 보여줘':
                output = {'state': 'clarification'}
            else:
                output = {'state': 'ready', 'conditions': {'metric': 'tutorial_rate', 'start': '2026-09-01', 'end': '2026-09-15',
                    'event_start': '2026-09-01', 'event_end': '2026-09-15', 'timezone': 'UTC', 'unit': 'user',
                    'numerator': 'completed_users', 'denominator': 'signup_users', 'group_by': 'channel', 'filters': {}, 'step': 3, 'period_basis': 'explicit_dates'}}
        else:
            execution = context['executions'][0]
            assert execution['status'] == 'success'
            assert execution['result']['rows']
            output = {'criteria': [{'id': row['id'], 'grade': 3, 'evidence_refs': [f"execution:{execution['execution_id']}", f"report:{context['report']['version']}"],
                'reason': '실제 실행 근거와 공개 조건을 연결', 'improvement': '추가 로그 수집을 확인'} for row in context['public_task']['rubric']['criteria']]}
        return {'state': 'completed', 'text': json.dumps(output), 'usage': {'input_tokens': 1, 'output_tokens': 1}, 'model': 'scripted'}


@pytest.fixture
def flow():
    admin, learner, records = (os.getenv(name) for name in ('DISCORD_TEST_ADMIN_DSN', 'DISCORD_TEST_LEARNER_DSN', 'DISCORD_TEST_RECORDS_DSN'))
    if not all((admin, learner, records)):
        pytest.skip('Requires explicitly isolated Discord PostgreSQL DSNs')
    settings = SimpleNamespace(admin_dsn=admin, learner_dsn=learner, query_timeout_ms=5000, max_rows=1000,
        max_bytes=1048576, preview_rows=1, execution_ttl=600, daily_call_limit=30)
    store = DiscordStore(records)
    store.initialize()
    provider = ScriptedProvider()
    engine = DiscordQueryEngine(provider, SqlRunner(settings), settings)
    service = DiscordTrainingService(store, engine, provider, settings)
    owner = uuid.uuid4().hex
    session = service.start(owner, 'guild', 'parent', uuid.uuid4().hex)
    assert session.get('state') == 'analysis', session
    yield service, provider, owner, session
    import psycopg
    from psycopg import sql
    for item in service.list_sessions(owner, 'guild'):
        with psycopg.connect(admin) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(item['schema_name'])))


def event(service, owner, sid, action, text='', payload=None):
    return service.handle(owner, sid, uuid.uuid4().hex, action, text, payload)


def test_actual_query_clarification_evidence_report_evaluation_restart(flow):
    service, provider, owner, session = flow
    sid = session['session_id']
    assert '공개 과제와 평가 조건' in service.resume(owner, 'guild', sid)['messages'][1]
    response = event(service, owner, sid, 'query', '비율을 보여줘')
    assert response['session']['pending_query'] and not response['session']['executions']
    assert not response['session']['help_history']  # neutral intermediate confirmation
    response = event(service, owner, sid, 'query', '공개 기간 가입자 중 3단계 완료자 비율, 채널별 비교')
    execution = response['session']['executions'][0]
    assert len(execution['result']['rows']) == 2  # full collection retained despite preview_rows=1
    assert execution['result']['result_complete']
    assert '실행 성공' in response['messages'][0]
    qid = execution['execution_id']
    before = provider.calls
    event(service, owner, sid, 'sql', payload={'execution_id': qid})
    assert provider.calls == before
    event(service, owner, sid, 'evidence', payload={'execution_id': qid})
    event(service, owner, sid, 'help', '중복과 사용자 차이를 설명해줘')
    event(service, owner, sid, 'hypothesis', '채널 구성 변화 가능성, 채널별 차이가 없다면 가설을 약화시킨다.')
    event(service, owner, sid, 'quality', '재도전 이벤트를 고유 사용자로 중복 제거한다. 수집 누락은 추가 로그 확인 필요.')
    event(service, owner, sid, 'report', '채널별 실제 근거를 비교했다. 인과는 확정 못하며 대응 전 실험과 로그를 확인한다.')
    event(service, owner, sid, 'followup', '수집 검증을 먼저 하고 완료율과 관측 상태를 모니터링한다.')
    submit_event = uuid.uuid4().hex
    result = service.handle(owner, sid, submit_event, 'submit')
    assert result['session']['state'] == 'completed', result
    assert result['session']['evaluations'][0]['result']['total'] == 75
    assert not result['session']['learning'][0]['before_help']
    assert result['session']['learning'][0]['after_help']
    calls = provider.calls
    assert service.handle(owner, sid, submit_event, 'submit') == result
    assert provider.calls == calls
    restored = DiscordTrainingService(DiscordStore(service.store.dsn), service.engine, provider, service.settings).get_session(owner, sid)
    assert restored['executions'][0] == execution
    assert restored['reports'][0]['evidence_refs'] == [qid]
    assert restored['telemetry'][0]['cost'] is None
    assert restored['queries'][1]['original_text'] == '비율을 보여줘'
    next_session = service.start(owner, 'guild', 'parent', uuid.uuid4().hex)
    assert next_session['task']['variant'] == 'followup'


def test_model_failure_preserves_report_and_no_false_evaluation(flow):
    service, provider, owner, session = flow
    sid = session['session_id']
    event(service, owner, sid, 'query', '公開期間の完了率')
    event(service, owner, sid, 'report', '判断保留')
    event(service, owner, sid, 'followup', '追加情報を確認する')
    provider.fail = True
    result = event(service, owner, sid, 'submit')
    assert result['session']['state'] == 'reporting'
    assert result['session']['evaluations'][0]['result']['held']
    assert result['session']['evaluations'][0]['result']['total'] is None
    assert result['session']['executions']
    failed = event(service, owner, sid, 'query', '조회')
    assert len(failed['session']['executions']) == 1


def test_beginner_confirmation_records_assistance(flow):
    service, provider, owner, session = flow
    beginner = service.start(owner, 'guild', 'parent', uuid.uuid4().hex, difficulty='beginner')
    result = event(service, owner, beginner['session_id'], 'query', '비율을 보여줘')
    assert result['session']['help_history'][0]['type'] == 'concept_hint'
    assert '선택지' in result['messages'][0]


def test_capture_failure_and_budget_do_not_publish_unsaved_success(flow, monkeypatch):
    service, provider, owner, session = flow
    sid = session['session_id']
    def unavailable(*args):
        raise RuntimeError('result disappeared')
    monkeypatch.setattr(service.engine.runner, 'get', unavailable)
    failed = event(service, owner, sid, 'query', '공개 기간 완료율')
    assert not failed['session']['executions']
    assert '영구 근거 확보에 실패' in failed['messages'][0]
    assert failed['session']['queries'][0]['outcome']['reason'] == 'result_capture_failed'
    service.daily_limit = 1
    count = provider.calls
    blocked = event(service, owner, sid, 'query', '공개 기간 완료율')
    assert provider.calls == count
    assert '호출 한도' in blocked['messages'][0]
    assert service.resume(owner, 'guild', sid)['session']['queries']
