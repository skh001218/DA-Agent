import asyncio
import copy
import json
from unittest.mock import Mock

import pytest

from da_agent.discord_terms import GLOSSARY, GUIDANCE, UNAVAILABLE, explain_terms
from test_discord_answers import flow, event
from test_discord_transport import setup


@pytest.mark.parametrize('question', ['ads와 organic이 뭐야?', 'ADS, ORGANIC', '광고 유입과 자연 유입 설명해줘', 'ads와 organic의 차이가 뭐야?'])
def test_acquisition_terms_are_short_and_need_no_model(question):
    provider = Mock()
    answer = explain_terms(question, {}, provider)
    assert len(answer) == 2
    assert '유료 광고' in answer[0] and '자연 유입' in answer[1]
    assert all(1 <= len(block.splitlines()) <= 3 for block in answer)
    provider.review.assert_not_called()


def test_alias_duplicates_boundaries_and_longest_match():
    provider = Mock()
    assert len(explain_terms('ads, ADS, 광고 유입', {}, provider)) == 1
    assert len(explain_terms('D7 리텐션', {}, provider)) == 1
    provider.review.return_value = {'state': 'error'}
    assert explain_terms('adsense', {}, provider) == [UNAVAILABLE]
    provider.review.assert_called_once()


@pytest.mark.parametrize('question', ['', '   ', 'a' * 501, 'ads organic dau mau arpu arppu'])
def test_invalid_input_does_not_call_model(question):
    provider = Mock()
    assert explain_terms(question, {}, provider) == [GUIDANCE]
    provider.review.assert_not_called()


def test_every_curated_definition_meets_length_contract():
    for names, lines in GLOSSARY.values():
        assert 1 <= len(lines) <= 3
        assert all(len(line) <= 120 for line in lines)


def test_unknown_and_mixed_question_uses_only_public_context_and_bounds_lines():
    provider = Mock()
    provider.review.return_value = {'state': 'completed', 'text': json.dumps({'terms': [
        {'term': 'ROAS', 'lines': ['광고 매출/광고 비용', '예시\n주의', '추가' * 100]}]})}
    task = {'dictionary': {'users': {'description': '공개'}}, 'timezone': 'UTC',
            'private_answer': 'secret', 'reports': ['private report']}
    answer = explain_terms('ads와 ROAS가 뭐야?', task, provider)
    envelope = json.loads(provider.review.call_args.args[0][-1]['content'])
    assert envelope == {'question': 'ads와 ROAS가 뭐야?', 'dictionary': task['dictionary'], 'timezone': 'UTC'}
    assert len(answer[0].splitlines()) == 3
    assert all(len(line) <= 120 for line in answer[0].splitlines())


@pytest.mark.parametrize('result', [
    {'state': 'error', 'reason': 'call_limit'}, {'state': 'completed', 'text': 'not json'},
    {'state': 'completed', 'text': '{"terms":[]}'},
    {'state': 'completed', 'text': '{"terms":[{"term":"x","lines":[null]}]}'},
    {'state': 'completed', 'text': '{"terms":[{"term":"x","lines":[]}]}'},
    {'state': 'completed', 'text': json.dumps({'terms': [{'term': 'x', 'lines': ['a']}] * 6})},
])
def test_model_failure_does_not_echo_raw_output(result):
    provider = Mock()
    provider.review.return_value = result
    assert explain_terms('미등록용어', {}, provider) == [UNAVAILABLE]


def test_term_question_keeps_pending_analysis_and_replays_without_model(flow):
    event(flow, 'query', '완료율 조회')
    before = flow[0].get_session('owner', flow[2])
    model_calls = flow[1].review.call_count
    answer = event(flow, 'question', 'ads와 organic이 뭐야?', eid='term-event')
    for key in ('state', 'pending_question', 'pending_query', 'queries', 'executions', 'selected_evidence', 'reports', 'evaluations'):
        assert answer['session'][key] == before[key]
    assert answer['session']['messages'][-1]['help_type'] == 'term_question'
    assert answer['session']['help_history'][-1]['type'] == 'term_question'
    assert flow[1].review.call_count == model_calls
    assert event(flow, 'question', 'ads와 organic이 뭐야?', eid='term-event') == answer
    flow[3].assert_not_called()
    continued = event(flow, 'answer', '과제 기간, UTC')
    envelope = json.loads(flow[1].review.call_args.args[0][-1]['content'])
    assert envelope['pending_query']['request'] == before['pending_query']['text']
    assert envelope['pending_query']['question'] == before['pending_query']['plan']['question']
    assert continued['session']['queries'][-1]['user_answer'] == '과제 기간, UTC'


@pytest.mark.parametrize('state', ['completed', 'stopped'])
def test_terms_available_in_archived_states(flow, state):
    service, _, sid, _ = flow
    service.store.documents[sid]['state'] = state
    answer = event(flow, 'question', 'organic')
    assert '자연 유입' in answer['messages'][0]
    assert answer['session']['state'] == state


def test_fallback_is_metered_and_failure_preserves_question(flow):
    event(flow, 'query', '완료율 조회')
    before = copy.deepcopy(flow[0].store.documents[flow[2]]['pending_question'])
    flow[0].store.reserve_call = Mock()
    flow[1].review.side_effect = RuntimeError('secret provider details')
    answer = event(flow, 'question', 'ROAS')
    flow[0].store.reserve_call.assert_called_once_with('owner', 30)
    assert answer['messages'] == [UNAVAILABLE]
    assert answer['session']['pending_question'] == before
    assert answer['session']['telemetry'][-1]['state'] == 'error'


def test_transport_routes_question_through_owner_checks():
    service, gateway, transport, event_ = setup(channel=30)
    service.session['thread_id'] = '30'
    asyncio.run(transport.command(event_, 'question', text='organic'))
    assert service.calls[-1][0] == 'handle'
    assert service.calls[-1][1][-1] == 'question'
    service, gateway, transport, event_ = setup(owner=2, channel=30)
    service.session['thread_id'] = '30'
    asyncio.run(transport.command(event_, 'question', text='organic'))
    assert not any(call[0] == 'handle' for call in service.calls)
    assert '자신의' in gateway.replies[0]
