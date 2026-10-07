from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
import asyncio

import pytest
import sqlglot

from da_agent.discord_generated_query import GeneratedQueryEngine, compile_public_select
from da_agent.discord_tables import result_table
from da_agent.discord_service import DiscordTrainingService, format_result
from da_agent.errors import DomainError


TASK = dict(generation_version='discord-generation-v1', objective='상점 변경 전후 골드 비교',
    schema={'shop_profiles': {'id': 'bigint', 'shop_version': 'text', 'prior_mastery': 'text'},
            'account_currency': {'id': 'bigint', 'profile_id': 'bigint', 'early_spent': 'bigint', 'late_spent': 'bigint'}},
    dictionary={}, relationships=[dict(table='account_currency', column='profile_id', target_table='shop_profiles', target_column='id')],
    timezone='Asia/Seoul', period={}, metrics={}, hidden_answer='PRIVATE_ANSWER', private_recipe='PRIVATE_RECIPE')


def engine(payload):
    provider = Mock()
    provider.review.return_value = dict(state='completed', text=json.dumps(payload), model='test')
    return GeneratedQueryEngine(provider, Mock(), NS())


def ready(query):
    return dict(state='ready', conditions=dict(operation='select', sql=query))


@pytest.mark.parametrize('query', [
    'SELECT * FROM shop_profiles ORDER BY id',
    "SELECT id FROM shop_profiles WHERE prior_mastery = 'beginner' ORDER BY id DESC",
    'SELECT p.shop_version, AVG(c.early_spent) AS early, AVG(c.late_spent) AS late, AVG(c.late_spent-c.early_spent) AS delta FROM account_currency c JOIN shop_profiles p ON c.profile_id=p.id GROUP BY p.shop_version',
    'WITH x AS (SELECT * FROM shop_profiles) SELECT id FROM x ORDER BY id',
    'SELECT shop_version, COUNT(*) AS n FROM shop_profiles GROUP BY shop_version ORDER BY n DESC',
    'SELECT STDDEV_POP(late_spent) AS variation FROM account_currency',
    'SELECT id FROM shop_profiles UNION SELECT profile_id FROM account_currency',
])
def test_public_rows_filters_comparisons_and_ctes_are_resolved(query):
    e = engine(ready(query))
    plan = e.resolve('현재 자료 조회', TASK)
    assert plan['state'] == 'ready'
    assert 'PRIVATE' not in json.dumps(e.provider.review.call_args.args[0])
    assert not e.runner.execute.called


def test_whole_table_expands_all_public_columns_without_adding_limit():
    compiled, tables = compile_public_select('SELECT * FROM shop_profiles ORDER BY id', TASK)
    assert tables == ['shop_profiles']
    assert all(name in compiled for name in TASK['schema']['shop_profiles'])
    assert 'LIMIT' not in compiled


@pytest.mark.parametrize('query', [
    'SELECT * FROM pg_roles',
    'SELECT * FROM other.shop_profiles',
    'SELECT item_name FROM shop_profiles',
    'SELECT p.secret FROM shop_profiles p',
    'SELECT id FROM shop_profiles p JOIN account_currency c ON p.id=c.profile_id',
    'SELECT * FROM shop_profiles; DELETE FROM shop_profiles',
    'SELECT * INTO stolen FROM shop_profiles',
    'SELECT * FROM shop_profiles FOR UPDATE',
    "SELECT pg_read_file('/etc/passwd') FROM shop_profiles",
    'WITH x AS (SELECT secret FROM shop_profiles) SELECT * FROM x',
    'WITH x AS (SELECT * FROM secret_data) SELECT * FROM x',
    'WITH RECURSIVE x AS (SELECT id FROM shop_profiles) SELECT * FROM x',
    'SELECT 1',
])
def test_unknown_or_unsafe_queries_never_reach_runner(query):
    e = engine(ready(query))
    assert e.resolve('조회', TASK)['reason'] == 'query_contract_invalid'
    assert not e.runner.execute.called


def test_missing_data_names_absent_information_and_current_schema():
    e = engine(dict(state='error', reason='missing_data', missing_data=['판매 아이템 목록', '상점 구성 변경 이력']))
    plan = e.resolve('상점의 판매 아이템 목록이 이전과 어떻게 달라졌어?', TASK)
    assert plan['reason'] == 'missing_data'
    assert all(word in plan['message'] for word in ['판매 아이템 목록', '변경 이력', 'shop_profiles', 'prior_mastery'])
    assert not e.runner.execute.called


@pytest.mark.parametrize('payload', [[], dict(state='error', reason='missing_data'),
    dict(state='error', reason='missing_data', missing_data=[]),
    dict(state='error', reason='missing_data', missing_data=[42])])
def test_malformed_provider_response_does_not_claim_missing_data(payload):
    assert engine(payload).resolve('조회', TASK)['reason'] == 'query_contract_invalid'


def test_clarification_keeps_request_and_answers_without_execution():
    e = engine(dict(state='clarification', question='어느 상점 구성을 조회할까요?'))
    pending = dict(request='해당 상점의 원본 데이터', answers=['변경 상점'])
    assert e.resolve('변경 상점', TASK, pending_query=pending)['state'] == 'clarification'
    context = json.loads(e.provider.review.call_args.args[0][-1]['content'])
    assert context['pending_query'] == pending
    assert not e.runner.execute.called


def test_select_executes_validated_plan_and_preserves_full_evidence():
    e = engine(ready('SELECT * FROM shop_profiles ORDER BY id'))
    plan = e.resolve('shop_profiles의 전체 데이터를 조회해줘', TASK)
    preview = dict(status='success', execution_id='e', rows=[[1, 'existing', 'beginner']], result_complete=True)
    full = dict(preview, rows=[[i, 'existing', 'beginner'] for i in range(200)])
    e.runner.execute.return_value = preview
    e.runner.get.return_value = dict(sql=plan['sql'], result=full)
    outcome = e.execute('s', 'pkg', plan, TASK)
    assert outcome['state'] == 'success' and len(outcome['full_result']['rows']) == 200
    assert e.runner.execute.call_args.kwargs['allowed_tables'] == ['shop_profiles']
    assert e.execute('s', 'pkg', dict(plan, sql='SELECT * FROM pg_roles'), TASK)['state'] == 'error'
    assert e.runner.execute.call_count == 1


def test_missing_data_clears_old_query_question_and_stores_diagnosis():
    e = engine(dict(state='error', reason='missing_data', missing_data=['판매 아이템 목록']))
    service = DiscordTrainingService(NS(reserve_call=lambda *a: None), NS(runner=e.runner), e.provider, NS(daily_call_limit=0))
    doc = dict(session_id='s', owner_user_id='o', task=TASK, help_level='independent', telemetry=[],
        conditions=None, queries=[], executions=[], messages=[], questions=[], pending_question=None,
        pending_query=dict(text='아이템 변화', plan=dict(question='기간은?'), answers=[]))
    message = service._query(doc, 'event', '전체 기간')[0]
    assert '판매 아이템 목록' in message and doc['pending_query'] is None
    assert doc['queries'][-1]['plan']['reason'] == 'missing_data'
    assert not e.runner.execute.called


@pytest.mark.parametrize('complete', [True, False])
def test_download_contains_every_collected_row_and_completeness(complete):
    execution = dict(execution_id='e', conditions=dict(operation='select', sql='SELECT * FROM shop_profiles'),
        data_version='v', result=dict(columns=['id'], rows=[[i] for i in range(200)],
            total_row_count=200 if complete else None, result_complete=complete))
    before = deepcopy(execution)
    table = result_table(execution)
    downloaded = json.loads(table['download']['content'])
    assert len(table['rows']) == 10 and downloaded['rows'] == execution['result']['rows']
    assert downloaded['result_complete'] is complete and execution == before
    text = format_result(execution, NS(max_rows=1000, max_bytes=1048576, query_timeout_ms=5000))
    assert 'JSON' in text and 'SELECT *' not in text
    assert ('불완전' in text) is (not complete)


def test_actual_gateway_sends_preview_and_full_data_as_separate_attachments():
    from da_agent.discord_bot import create_client
    client = create_client(Mock(), NS(guild_ids=(1,), message_content=False))
    files = []
    class Channel:
        async def send(self, *, file, allowed_mentions):
            files.append((file.filename, file.fp.read()))
            return NS(id=len(files))
    execution = dict(execution_id='e', conditions=dict(operation='select'),
        result=dict(columns=['id'], rows=[[i] for i in range(200)], result_complete=True, total_row_count=200))
    ids = asyncio.run(client.da_transport.gateway.send_table(Channel(), result_table(execution)))
    assert ids == ['1', '2'] and files[0][0].endswith('.png')
    assert files[0][1].startswith(b'\x89PNG')
    assert len(json.loads(files[1][1])['rows']) == 200


def test_failed_full_data_attachment_is_explicitly_reported():
    from da_agent.discord_transport import DiscordTransport
    messages = []
    class Gateway:
        async def send_table(self, *args):
            raise PermissionError('attachment denied')
        async def send(self, channel, text):
            messages.append(text)
    table = dict(title='조회 결과', columns=['id'], rows=[[1]], download=dict(filename='query-e.json', content='{}'))
    asyncio.run(DiscordTransport(None, Gateway(), [1])._emit(None, ['조회 성공'], tables=[table]))
    assert any('전체 데이터 파일 첨부를 전송하지 못했습니다' in m for m in messages)
    assert any('미리보기' in m for m in messages)
