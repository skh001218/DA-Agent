"""Conversation routing with real service/compiler and in-memory persistence."""
import asyncio
import copy
import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_query import DiscordQueryEngine
from da_agent.discord_transport import DiscordTransport


class MemoryStore:
    def __init__(self):
        self.documents, self.events = {}, {}

    def claim_event(self, event_id, *args, **kwargs):
        return self.events.get(event_id)

    def create(self, document, event_id, response):
        self.documents[document['session_id']] = copy.deepcopy(document)
        self.events[event_id] = copy.deepcopy(response)

    def get(self, owner, sid):
        assert self.documents[sid]['owner_user_id'] == owner
        return copy.deepcopy(self.documents[sid])

    def list(self, owner, guild):
        return [copy.deepcopy(d) for d in self.documents.values() if d['owner_user_id'] == owner]

    @contextmanager
    def edit(self, owner, sid):
        document = self.get(owner, sid)
        yield document, None
        self.documents[sid] = copy.deepcopy(document)

    def finish_event(self, eid, response, conn=None):
        self.events[eid] = copy.deepcopy(response)

    def reserve_call(self, *args):
        pass


def provider_result(state='ready', conditions=None):
    return {'state': 'completed', 'text': json.dumps({'state': state, 'conditions': conditions or {}}, ensure_ascii=False)}


CONDITIONS = {'metric': 'tutorial_rate', 'start': '2026-09-01', 'end': '2026-09-15',
    'timezone': 'UTC', 'period_basis': 'explicit_dates', 'unit': 'user', 'group_by': None,
    'filters': {}, 'step': 3, 'event_start': '2026-09-01', 'event_end': '2026-10-01',
    'numerator': 'completed_users', 'denominator': 'signup_users'}


@pytest.fixture
def flow(monkeypatch):
    provider = Mock()
    provider.review.return_value = provider_result('clarification', {'metric': 'tutorial_rate', 'step': 3})
    settings = NS(daily_call_limit=30, max_rows=1000, max_bytes=1048576, query_timeout_ms=5000)
    store = MemoryStore()
    service = DiscordTrainingService(store, NS(runner=Mock()), provider, settings,
        dataset_factory=lambda *args: {'schema_name': 'fixture'})
    session = service.start('owner', 'guild', 'parent', 'start', practice='analysis')
    result = {'state': 'success', 'sql': 'SELECT 1', 'full_result': {'status': 'success',
        'execution_id': 'execution1', 'columns': ['rate'], 'rows': [[75]], 'result_complete': True}}
    execute = Mock(return_value=result)
    monkeypatch.setattr(DiscordQueryEngine, 'execute', execute)
    return service, provider, session['session_id'], execute


def event(flow, action, text='', payload=None, eid=None):
    service, provider, sid, execute = flow
    return service.handle('owner', sid, eid or uuid.uuid4().hex, action, text, payload)


def envelope(flow):
    return json.loads(flow[1].review.call_args.args[0][-1]['content'])


def test_answer_keeps_original_query_and_question_context_after_restart(flow):
    first = event(flow, 'query', '튜토리얼 3단계 완료율')
    assert '/answer' in first['messages'][0]
    flow[1].review.return_value = provider_result('ready', CONDITIONS)
    restored = flow[0].get_session('owner', flow[2])
    assert restored['pending_question']['kind'] == 'query_conditions'
    second = event(flow, 'answer', '과제 기간, 신규 가입자 전체를 분모로, UTC 관측 종료까지')
    context = envelope(flow)
    assert context['pending_query']['request'] == '튜토리얼 3단계 완료율'
    assert context['pending_query']['question']
    assert context['pending_query']['proposed_conditions']['step'] == 3
    assert context['request'] == '과제 기간, 신규 가입자 전체를 분모로, UTC 관측 종료까지'
    assert second['session']['queries'][-1]['user_answer'] == context['request']
    assert second['session']['pending_question']['kind'] == 'analysis_reason'
    assert flow[3].call_count == 1


def test_mentor_answer_records_reason_without_query_or_model_call(flow):
    flow[1].review.return_value = provider_result('ready', CONDITIONS)
    event(flow, 'query', '완료율 조회')
    count = flow[1].review.call_count
    answer = event(flow, 'answer', '광고 유입 구성이 달라졌을 가능성을 비교하려고 합니다.', eid='reason-answer')
    assert not answer['session']['pending_question']
    assert len(answer['session']['queries']) == 1
    assert answer['session']['analysis_answers'][0]['text'].startswith('광고')
    assert flow[1].review.call_count == count
    repeated = event(flow, 'answer', '광고 유입 구성이 달라졌을 가능성을 비교하려고 합니다.', eid='reason-answer')
    assert repeated == answer


def test_new_query_supersedes_old_question_and_does_not_inherit_answers(flow):
    first = event(flow, 'query', '완료율 조회')
    old = first['session']['pending_question']['id']
    event(flow, 'query', '가입자 수 조회')
    context = envelope(flow)
    assert context['request'] == '가입자 수 조회' and context['pending_query'] is None
    assert context['clarification'] is None
    assert flow[0].get_session('owner', flow[2])['questions'][0]['id'] == old
    assert flow[0].get_session('owner', flow[2])['questions'][0]['state'] == 'superseded'


def test_api_failure_keeps_answers_for_retry_and_explains_error(flow):
    event(flow, 'query', '완료율 조회')
    flow[1].review.return_value = {'state': 'error', 'reason': 'api_unavailable'}
    failed = event(flow, 'answer', '분모는 신규 가입자 전체입니다.')
    assert '서비스 오류' in failed['messages'][0]
    assert failed['session']['pending_query']['answers'] == ['분모는 신규 가입자 전체입니다.']
    assert failed['session']['pending_question']['state'] == 'waiting'
    event(flow, 'answer', '기간은 과제 기간입니다.')
    assert envelope(flow)['pending_query']['answers'] == ['분모는 신규 가입자 전체입니다.', '기간은 과제 기간입니다.']


def test_reply_to_old_or_unrelated_message_is_rejected_without_model_call(flow):
    first = event(flow, 'query', '완료율 조회')
    flow[0].bind_question('owner', flow[2], first['session']['pending_question']['id'], ['100'])
    event(flow, 'query', '가입자 수 조회')
    count = flow[1].review.call_count
    for target in ['100', 'unknown']:
        rejected = event(flow, 'answer', '과제 기간입니다.', {'reply_to_message_id': target})
        assert '현재 답변을 기다리는 질문이 아닙니다' in rejected['messages'][0]
    assert flow[1].review.call_count == count


def test_reply_to_current_question_and_followup_answer(flow):
    first = event(flow, 'query', '완료율 조회')
    flow[0].bind_question('owner', flow[2], first['session']['pending_question']['id'], ['100'])
    event(flow, 'answer', 'UTC', {'reply_to_message_id': '100'})
    report = event(flow, 'report', '근거에 따라 원인을 보류하고 추가 로그를 확인한다.')
    assert report['session']['pending_question']['kind'] == 'followup'
    response = event(flow, 'answer', '추가 로그 검증을 먼저 수행한다.')
    assert response['session']['state'] == 'reporting'
    assert response['session']['reports'][-1]['followup_answers'][0]['text'].startswith('추가')


def test_answer_without_pending_question_does_not_start_query(flow):
    result = event(flow, 'answer', 'UTC입니다.')
    assert '질문이 없습니다' in result['messages'][0]
    flow[1].review.assert_not_called()


def test_dictionary_tables_do_not_consume_pending_answer_or_model_call(flow):
    first = event(flow, 'query', '완료율 조회')
    count = flow[1].review.call_count
    reference = event(flow, 'help', '', {'help_type': 'data_dictionary'})
    assert len(reference['tables']) == 3
    assert reference['session']['pending_question']['id'] == first['session']['pending_question']['id']
    assert flow[1].review.call_count == count


def test_query_answer_still_includes_result_table(flow):
    event(flow, 'query', '완료율 조회')
    flow[1].review.return_value = provider_result('ready', CONDITIONS)
    result = event(flow, 'answer', '과제 기간, 신규 가입자 전체 중 완료자 비율, UTC 관측 종료까지')
    assert result['tables'][0]['rows'] == [[75]]
    assert result['request_state'] == 'success'


def test_short_answer_preserves_already_known_conditions(flow):
    partial = {k: v for k, v in CONDITIONS.items() if k != 'timezone'}
    flow[1].review.return_value = provider_result('clarification', partial)
    event(flow, 'query', '조건이 정의된 완료율 조회')
    flow[1].review.return_value = provider_result('ready', {'timezone': 'UTC'})
    completed = event(flow, 'answer', 'UTC입니다.')
    assert completed['request_state'] == 'success'
    assert completed['session']['conditions'] == CONDITIONS


def test_legacy_unanswered_mentor_question_accepts_answer(flow):
    document = flow[0].store.documents[flow[2]]
    document['direction_prompted'] = True
    document['messages'].append({'role': 'mentor', 'text': '멘토: 이 비교를 선택한 이유와 가설을 알려주세요.'})
    result = event(flow, 'answer', '기능 검증: 유입 구성이 달라졌을 가능성을 확인하려고 했습니다.')
    assert len(result['session']['analysis_answers']) == 1
    assert result['session']['pending_question'] is None
    flow[1].review.assert_not_called()


def test_unreadable_reply_guides_to_answer_without_model_call(flow):
    service, provider, sid, execute = flow
    service.bind_thread('owner', sid, 'thread')
    async def validate(*args):
        pass
    sent = []
    async def send(channel, text):
        sent.append(text)
    gateway = NS(bot_user_id='bot', message_content_enabled=False,
        is_private_thread=lambda c: True, validate_thread=validate, send=send)
    transport = DiscordTransport(service, gateway, ['guild'])
    message = NS(id='reply', author=NS(id='owner', bot=False), guild=NS(id='guild'),
        channel=NS(id='thread'), content='', reference=NS(message_id='100'), mentions=[], attachments=[])
    asyncio.run(transport.message(message))
    assert '/answer' in sent[0]
    provider.review.assert_not_called()


@pytest.mark.parametrize('reply,mention', [(True, False), (False, True), (False, False)])
def test_transport_routes_only_replies_or_bot_mentions(flow, reply, mention):
    service, provider, sid, execute = flow
    service.bind_thread('owner', sid, 'thread')
    first = event(flow, 'query', '완료율 조회')
    service.bind_question('owner', sid, first['session']['pending_question']['id'], ['100'])
    gateway = NS(bot_user_id='bot', message_content_enabled=True,
                 is_private_thread=lambda c: True, validate_thread=Mock())
    async def validate(*args):
        pass
    async def send(*args):
        return NS(id=uuid.uuid4().hex)
    gateway.validate_thread, gateway.send = validate, send
    transport = DiscordTransport(service, gateway, ['guild'])
    message = NS(id='message1', author=NS(id='owner', bot=False), guild=NS(id='guild'),
        channel=NS(id='thread'), content='UTC 기준', attachments=[],
        reference=NS(message_id='100') if reply else None,
        mentions=[NS(id='bot')] if mention else [])
    count = provider.review.call_count
    asyncio.run(transport.message(message))
    assert provider.review.call_count == count + int(reply or mention)


def test_transport_emits_and_persists_discord_question_ids(flow):
    first = event(flow, 'query', '완료율 조회')
    async def send(*args):
        return NS(id=123)
    transport = DiscordTransport(flow[0], NS(send=send), ['guild'])
    asyncio.run(transport._emit(NS(id='thread'), first['messages'], first['session']))
    assert flow[0].get_session('owner', flow[2])['pending_question']['discord_message_ids'] == ['123']
