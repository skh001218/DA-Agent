import json
import pytest
from types import SimpleNamespace as NS

from da_agent.discord_education import representative_task
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_query import DiscordQueryEngine


class Provider:
    def __init__(self, responses):
        self.responses, self.contexts = iter(responses), []

    def review(self, messages):
        self.contexts.append(json.loads(messages[-1]['content']))
        payload = next(self.responses)
        if payload.get('state') == 'error' and payload.get('reason', '').startswith('api_'):
            return payload
        return {'state': 'completed', 'text': json.dumps(payload)}


class Runner:
    def __init__(self):
        self.calls = []

    def execute(self, session_id, schema, sql, **kwargs):
        self.calls.append(sql)
        self.result = {'status': 'success', 'execution_id': 'exec1', 'rows': [[40, 30, 75]],
                       'columns': ['denominator', 'numerator', 'rate_percent'], 'result_complete': True}
        return self.result

    def get(self, *args):
        return {'sql': self.calls[-1], 'result': self.result}


def setup(responses):
    provider, runner = Provider(responses), Runner()
    settings = NS(query_timeout_ms=5000, max_rows=1000, max_bytes=1048576)
    service = DiscordTrainingService(NS(reserve_call=lambda *args: None), NS(runner=runner), provider, settings)
    doc = dict(session_id='s', schema_name='schema', owner_user_id='owner', task=representative_task(),
               pending_query=None, conditions=None, telemetry=[], help_level='independent',
               queries=[], executions=[], messages=[], help_history=[], data_version='v')
    return service, doc, provider, runner


def rate_conditions():
    return dict(metric='tutorial_rate', start='2026-09-01', end='2026-09-15', timezone='UTC',
                period_basis='explicit_dates', unit='user', numerator='completed_users', denominator='signup_users',
                event_start='2026-09-01', event_end='2026-10-01', step=3, group_by=None, filters={})


def test_latest_request_replaces_pending_and_executes_new_metric():
    service, doc, provider, runner = setup([
        {'state': 'clarification', 'conditions': dict(metric='attempts_count', unit='attempt')},
        {'state': 'ready', 'conditions': rate_conditions(), 'replaces_pending': True}])
    service._query(doc, '1', '도전 수를 보여줘')
    messages = service._query(doc, '2', '과제 기간 신규 가입자의 3단계 완료율을 보여줘')
    assert provider.contexts[-1]['request'] == '과제 기간 신규 가입자의 3단계 완료율을 보여줘'
    assert provider.contexts[-1]['pending_query']['request'] == '도전 수를 보여줘'
    assert doc['conditions']['metric'] == 'tutorial_rate' and doc['pending_query'] is None
    assert len(runner.calls) == 1 and '실행 성공' in messages[0]


def test_server_failure_preserves_answer_and_is_not_blame_on_user():
    service, doc, provider, runner = setup([
        {'state': 'clarification', 'conditions': dict(metric='tutorial_rate')},
        {'state': 'error', 'reason': 'api_unavailable', 'provider_diagnostic': {'http_status': 500}},
        {'state': 'ready', 'conditions': rate_conditions()}])
    service._query(doc, '1', '완료율')
    answer = '과제 기간 신규 가입자 전체 중 3단계 완료자, 관측 종료까지'
    messages = service._query(doc, '2', answer)
    assert '모델 서비스 오류' in messages[0] and '명시해' not in messages[0]
    assert doc['pending_query']['answers'] == [answer] and not runner.calls
    service._query(doc, '3', '같은 요청으로 재시도')
    assert provider.contexts[-1]['pending_query']['answers'] == [answer, '같은 요청으로 재시도']
    assert len(runner.calls) == 1


def test_force_new_query_drops_unconfirmed_context():
    service, doc, provider, runner = setup([
        {'state': 'clarification', 'conditions': dict(metric='attempts_count')},
        {'state': 'clarification', 'conditions': dict(metric='tutorial_rate')}])
    service._query(doc, '1', '도전 수')
    service._query(doc, '2', '완료율', new_query=True)
    assert provider.contexts[-1]['pending_query'] is None
    assert provider.contexts[-1]['clarification'] is None
    assert doc['pending_query']['text'] == '완료율'


def test_question_only_asks_for_missing_denominator_and_ignores_model_prose():
    c = rate_conditions()
    del c['denominator']
    provider = Provider([{'state': 'clarification', 'conditions': c, 'question': 'PRIVATE_ANSWER'}])
    result = DiscordQueryEngine(provider, None, None).resolve('조회', representative_task())
    assert '분모' in result['question'] and '기간' not in result['question']
    assert 'PRIVATE' not in result['question']


def test_unsupported_query_explains_limits_without_sql():
    service, doc, provider, runner = setup([{'state': 'error', 'reason': 'unsupported_query'}])
    messages = service._query(doc, '1', '유저별 단계별 최초 완료까지 도전 횟수')
    assert '아직 지원하지 않습니다' in messages[0] and not runner.calls
    assert doc['pending_query'] is None


def test_explicit_task_period_and_metric_change_do_not_require_model_flags():
    c = rate_conditions()
    del c['start']
    del c['end']
    provider = Provider([{'state': 'ready', 'conditions': c}])
    plan = DiscordQueryEngine(provider, None, None).resolve('과제 기간 완료율', representative_task(),
        pending_query={'request': '도전 수', 'answers': [], 'proposed_conditions': {'metric': 'attempts_count'}})
    assert plan['state'] == 'ready' and plan['conditions']['start'] == '2026-09-01'
    assert plan['replaces_pending'] is True


def test_public_period_is_not_assumed_for_unspecified_dates():
    c = rate_conditions()
    del c['start']
    del c['end']
    plan = DiscordQueryEngine(Provider([{'state': 'ready', 'conditions': c}]), None, None).resolve('완료율', representative_task())
    assert plan['state'] == 'clarification'


def test_different_metric_does_not_inherit_old_unconfirmed_task_period():
    c = rate_conditions()
    del c['start']
    del c['end']
    plan = DiscordQueryEngine(Provider([{'state': 'ready', 'conditions': c}]), None, None).resolve('완료율을 새로 조회', representative_task(),
        pending_query={'request': '과제 기간 도전 수', 'answers': [], 'proposed_conditions': {'metric': 'attempts_count'}})
    assert plan['state'] == 'clarification' and '기간' in plan['question']


@pytest.mark.parametrize('reason', ['api_timeout', 'api_key_invalid', 'api_permission_denied', 'api_invalid_response'])
def test_other_provider_errors_are_explained_as_service_errors(reason):
    service, doc, _, runner = setup([{'state': 'error', 'reason': reason}])
    assert '모델 서비스 오류' in service._query(doc, '1', '가입자 수 조회')[0]
    assert not runner.calls
