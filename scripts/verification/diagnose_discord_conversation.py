"""Historical pre-fix reproducer; its gap assertions are expected to fail after repair.

For current behavior, run tests/automated/test_discord_answers.py instead.
"""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_query import DiscordQueryEngine
from da_agent.discord_education import representative_task


def diagnose():
    service = DiscordTrainingService(Mock(), SimpleNamespace(runner=Mock()), Mock(), SimpleNamespace(daily_call_limit=30))
    base = {'pending_query': None, 'owner_user_id': 'diagnostic', 'session_id': 'diagnostic',
            'task': representative_task(), 'conditions': None, 'help_level': 'independent',
            'telemetry': [], 'queries': [], 'messages': [], 'help_history': []}
    engine = Mock()
    engine.resolve.return_value = {'state': 'error', 'reason': 'unsupported_query'}
    doc = copy.deepcopy(base)
    doc['direction_prompted'] = True
    doc['messages'] = [{'role': 'mentor', 'text': '이 비교를 선택한 이유와 검토할 가설을 설명해주세요.'}]
    with patch('da_agent.discord_query.DiscordQueryEngine', return_value=engine):
        service._query(doc, '1', '광고 유입 비중 변화가 원인일 수 있다고 생각했기 때문입니다.')
    args = engine.resolve.call_args
    result = {'mentor_answer_routed_as_new_query': {
        'request': args.args[0], 'clarification': args.kwargs['clarification'],
        'saved_user_answer': doc['queries'][-1]['user_answer']}}
    assert args.kwargs['clarification'] is None
    assert doc['queries'][-1]['user_answer'] is None

    provider = Mock()
    provider.review.return_value = {'state': 'error', 'reason': 'api_unavailable'}
    DiscordQueryEngine(provider, Mock(), Mock()).resolve('완료율 조회', base['task'], clarification='UTC 기준')
    envelope = json.loads(provider.review.call_args.args[0][-1]['content'])
    result['clarification_model_input_keys'] = sorted(envelope)
    assert 'messages' not in envelope and 'proposed_conditions' not in envelope and 'question' not in envelope

    engine.reset_mock()
    engine.resolve.return_value = {'state': 'error', 'reason': 'api_unavailable'}
    doc = copy.deepcopy(base)
    doc['pending_query'] = {'text': '완료율 조회',
                            'plan': {'state': 'clarification', 'question': '분모를 알려주세요.'}, 'answers': []}
    with patch('da_agent.discord_query.DiscordQueryEngine', return_value=engine):
        service._query(doc, '2', '분모는 신규 가입 고유 사용자입니다.')
        service._query(doc, '3', '기간은 9월 1일부터 15일 미만입니다.')
    result['answer_after_failed_interpretation'] = {
        'pending_answers': doc['pending_query']['answers'],
        'next_clarification': engine.resolve.call_args.kwargs['clarification'],
        'pending_context_forwarded_to_engine': 'pending_query' in engine.resolve.call_args.kwargs}
    assert '신규 가입' not in engine.resolve.call_args.kwargs['clarification']
    return result


if __name__ == '__main__':
    result = diagnose()
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(serialized, encoding='utf-8')
    print(serialized)
