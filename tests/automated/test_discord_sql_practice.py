"""Direct SQL with real isolated PostgreSQL and message orchestration."""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest
import psycopg
from psycopg import sql

from da_agent.discord_bot import DiscordSettings, create_client
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_sql_practice import extract_sql, reference_sql, TEMPLATE
from da_agent.discord_store import DiscordStore
from da_agent.discord_transport import DiscordTransport
from da_agent.errors import DomainError
from da_agent.sql_runner import SqlRunner


@pytest.fixture
def flow():
    admin, learner, records = [os.getenv(k) for k in ('DISCORD_TEST_ADMIN_DSN', 'DISCORD_TEST_LEARNER_DSN', 'DISCORD_TEST_RECORDS_DSN')]
    if not all((admin, learner, records)):
        pytest.skip('Requires disposable PostgreSQL DSNs')
    settings = NS(admin_dsn=admin, learner_dsn=learner, query_timeout_ms=5000, max_rows=1000,
        max_bytes=1048576, preview_rows=1, execution_ttl=600, daily_call_limit=30)
    store = DiscordStore(records); store.initialize()
    provider = Mock()
    service = DiscordTrainingService(store, NS(runner=SqlRunner(settings)), provider, settings)
    owner = uuid4().hex
    yield service, owner
    for session in service.list_sessions(owner, '10'):
        schemas = {session['schema_name']} | {c['schema_name'] for c in session.get('sql_checks', [])}
        with psycopg.connect(admin) as conn:
            for schema in schemas:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def start(flow, level='intermediate', **kwargs):
    service, owner = flow
    document = service.start(owner, '10', '20', uuid4().hex, practice='sql', difficulty=level, **kwargs)
    assert 'session_id' in document, document
    return document


def handle(flow, document, action, text='', payload=None, eid=None):
    return flow[0].handle(flow[1], document['session_id'], eid or uuid4().hex, action, text, payload)


def block(query):
    return '```sql\n' + query + '\n```'


@pytest.mark.parametrize('value', ['', '```sql\n-- comment\n```', '```python\nSELECT 1\n```',
    '```sql\nSELECT 1', '```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```', 'x' * 1901])
def test_invalid_blocks(value):
    with pytest.raises(DomainError):
        extract_sql(value)


def test_required_choice_before_event_or_dataset():
    store = Mock(); factory = Mock()
    service = DiscordTrainingService(store, Mock(), Mock(), NS(), dataset_factory=factory)
    for value in (None, '', 'other'):
        assert service.start('u','10','20','e',practice=value)['state'] == 'failed'
    store.claim_event.assert_not_called(); factory.assert_not_called()


def test_registration_requires_practice(tmp_path):
    async def check():
        client = create_client(Mock(), DiscordSettings('fake',(10,),'r','a','l',tmp_path / 'key'))
        param = next(p for p in client.da_command_tree.get_command('training').parameters if p.name == 'practice')
        assert param.required
        assert [(c.name,c.value) for c in param.choices] == [('SQL 연습','sql'),('분석 연습','analysis')]
        assert client.da_command_tree.get_command('sqlrun')
        assert not next(p for p in client.da_command_tree.get_command('submit').parameters if p.name == 'execution_id').required
        await client.close()
    asyncio.run(check())


@pytest.mark.parametrize('level', ['beginner', 'intermediate', 'advanced'])
def test_real_database_equivalent_sql_final_result_and_restart(flow, level, tmp_path):
    document = start(flow, level)
    query = reference_sql(document['task'])
    # A CTE rewrite is an independently submitted equivalent form.
    query = 'WITH answer AS (' + query + ') SELECT * FROM answer'
    eid = uuid4().hex
    response = handle(flow, document, 'sqlrun', block(query), eid=eid)
    attempt = response['session']['sql_attempts'][-1]
    assert attempt['sql'] == query and attempt['result']['status'] == 'success'
    assert attempt['full_result']['result_complete']
    replay = handle(flow, document, 'sqlrun', block('SELECT 9'), eid=eid)
    assert len(replay['session']['sql_attempts']) == 1
    evaluation = handle(flow, document, 'submit')
    assert evaluation['session']['state'] == 'completed'
    criteria = evaluation['session']['evaluations'][-1]['result']['criteria']
    assert all(c['status'] == '충족' for c in criteria if c['id'] != 'verification')
    if level == 'advanced':
        assert next(c for c in criteria if c['id']=='verification')['status']=='보완 필요'
    assert evaluation['submission']['practice'] == 'sql'
    public = str(evaluation['submission'])
    assert 'sql_checks' not in public
    assert all(c['schema_name'] not in public for c in document['sql_checks'])
    # Native PDF rendering must accept the SQL-specific publication payload.
    from da_agent.discord_pdf import render_submission_pdf
    pdf = render_submission_pdf(evaluation['submission'])
    assert pdf.startswith(b'%PDF')
    restarted = DiscordTrainingService(DiscordStore(flow[0].store.dsn), flow[0].engine, Mock(), flow[0].settings)
    saved = restarted.resume(flow[1], '10', document['session_id'])
    assert saved['session']['sql_attempts'][0]['sql'] == query
    assert 'sql 코드 블록' in '\n'.join(saved['messages'])
    flow[0].provider.review.assert_not_called()


def test_error_revision_latest_failed_explicit_selection_and_versions(flow):
    document = start(flow)
    handle(flow, document, 'sqlrun', block(reference_sql(document['task'])))
    first = flow[0].get_session(flow[1], document['session_id'])['sql_attempts'][-1]
    failed = handle(flow, document, 'sqlrun', block('SELECT nonexistent FROM users'))
    assert failed['session']['sql_attempts'][-1]['result']['status'] == 'error'
    assert '수정한 전체 SQL' in failed['messages'][0]
    assert 'submission' not in handle(flow, document, 'submit')
    assert 'submission' not in handle(flow, document, 'submit', payload={'execution_id':'other'})
    chosen = handle(flow, document, 'submit', payload={'execution_id':first['execution_id']})
    assert chosen['submission']['completed']
    assert 'submission' not in handle(flow, document, 'submit', payload={'execution_id':'other'})
    handle(flow, document, 'sqlrun', block(reference_sql(document['task'])))
    revised = handle(flow, document, 'submit')
    assert len(revised['session']['evaluations']) == 2
    assert len(revised['session']['sql_attempts']) == 3


def test_wrong_counts_policy_and_solution_exposure(flow):
    document = start(flow, 'beginner')
    assert '먼저' in handle(flow, document, 'help', payload={'help_type':'solution'})['messages'][0]
    blocked = handle(flow, document, 'sqlrun', block('DELETE FROM users'))
    assert blocked['session']['sql_attempts'][-1]['result']['status'] == 'blocked'
    # A constant answer that matches only the main fixture fails alternate data.
    correct = handle(flow, document, 'sqlrun', block(reference_sql(document['task'])))
    den, num, rate = correct['session']['sql_attempts'][-1]['full_result']['rows'][0]
    handle(flow, document, 'sqlrun', block(f'SELECT {den} AS denominator, {num} AS numerator, {rate}::numeric AS completion_rate'))
    result = handle(flow, document, 'submit')
    assert result['session']['evaluations'][-1]['result']['criteria'][-1]['status'] == '보완 필요'
    shown = handle(flow, document, 'help', payload={'help_type':'solution'})
    assert shown['session']['sql_exposure'] == '열람 확인'
    assert shown['messages'][0].startswith('SELECT')
    assert '/sqlrun' in handle(flow, document, 'query', '정답 실행')['messages'][0]


def test_partial_results_hold_and_empty_block_no_attempt(flow):
    document = start(flow)
    handle(flow, document, 'sqlrun', TEMPLATE)
    assert not flow[0].get_session(flow[1],document['session_id'])['sql_attempts']
    flow[0].settings.max_rows = 1
    handle(flow, document, 'sqlrun', block(reference_sql(document['task'])))
    response = handle(flow, document, 'submit')
    assert response['session']['evaluations'][-1]['result']['held']
    assert not response['submission']['completed']


def test_verification_connection_failure_is_held(flow):
    document = start(flow, 'beginner')
    handle(flow, document, 'sqlrun', block(reference_sql(document['task'])))
    # Break only learner verification connections after preserving the main result.
    flow[0].settings.learner_dsn = 'postgresql://discord_learner:unused@127.0.0.1:1/discord_test'
    response = handle(flow, document, 'submit')
    assert response['session']['evaluations'][-1]['result']['held']
    assert not response['submission']['completed']


def test_join_duplicates_and_event_period_errors(flow):
    document = start(flow, 'advanced')
    correct = reference_sql(document['task'])
    wrong = correct.replace('COUNT(DISTINCT c.user_id)', 'COUNT(c.user_id)').replace('SELECT DISTINCT user_id FROM tutorial_attempts', 'SELECT user_id FROM tutorial_attempts')
    handle(flow, document, 'sqlrun', block(wrong))
    result = handle(flow, document, 'submit')['session']['evaluations'][-1]['result']
    assert next(c for c in result['criteria'] if c['id'] == 'duplicates')['status'] == '보완 필요'
    wrong = correct.replace("AND attempt_at < '2026-10-01'::timestamptz", "AND attempt_at <= '2026-10-01'::timestamptz")
    handle(flow, document, 'sqlrun', block(wrong))
    result = handle(flow, document, 'submit')['session']['evaluations'][-1]['result']
    assert next(c for c in result['criteria'] if c['id'] == 'period')['status'] == '보완 필요'


def test_source_connection_legacy_and_exposure(flow):
    service, owner = flow
    source = service.start(owner,'10','20',uuid4().hex,practice='analysis')
    with service.store.edit(owner,source['session_id']) as (doc,conn):
        doc.pop('practice'); doc['state'] = 'completed'
        doc['help_history'].append({'type':'sql_view'})
    linked = start(flow, source_session_id=source['session_id'])
    assert linked['sql_exposure'] == '열람 확인'
    assert linked['source_session_id'] == source['session_id']
    assert service.get_session(owner,source['session_id'])['state'] == 'completed'
    with pytest.raises(DomainError):
        service.start('other','10','20',uuid4().hex,practice='sql',source_session_id=source['session_id'])


@pytest.mark.parametrize('attachments', [False, True])
def test_native_template_reply_routing_wrong_target_and_duplicate(flow, attachments):
    from test_discord_transport import Gateway
    class Messages(Gateway):
        async def send(self, channel, text):
            self.sent.append((channel.id,text))
            return NS(id=1000 + len(self.sent))
        async def send_table(self, channel, table):
            if not attachments:
                raise RuntimeError('attachment unavailable')
            sent = await self.send(channel, '[table attachment]')
            return [str(sent.id)]
    service, owner = flow
    document = start(flow)
    thread_id = str(uuid4().int % (10**17))
    service.bind_thread(owner, document['session_id'], thread_id)
    gateway = Messages()
    gateway.thread.id = int(thread_id)
    transport = DiscordTransport(service,gateway,[10])
    response = service.resume(owner,'10',document['session_id'])
    asyncio.run(transport._emit(gateway.thread,response['messages'],response['session']))
    assert any(text == TEMPLATE for _,text in gateway.sent)
    updated = service.get_session(owner,document['session_id'])
    target = next(iter(updated['sql_reply_targets']))
    message = NS(id=uuid4().hex,author=NS(id=owner,bot=False),guild=NS(id=10),channel=gateway.thread,
        content=block(reference_sql(document['task'])),reference=NS(message_id='unrelated'),mentions=[])
    asyncio.run(transport.message(message))
    assert not service.get_session(owner,document['session_id'])['sql_attempts']
    message.reference.message_id=target
    asyncio.run(transport.message(message)); asyncio.run(transport.message(message))
    saved = service.get_session(owner,document['session_id'])
    assert len(saved['sql_attempts']) == 1
    assert saved['sql_attempts'][0]['reply_to_message_id'] == target
    assert len(saved['sql_reply_targets']) > 1
    # The last emitted table fallback is also a valid reply target, not just
    # the preceding execution-ID text. Real attachment IDs follow this path too.
    table_target = str(1000 + len(gateway.sent))
    assert table_target in saved['sql_reply_targets']
    message.id = uuid4().hex
    message.reference.message_id = table_target
    asyncio.run(transport.message(message))
    saved = service.get_session(owner, document['session_id'])
    assert len(saved['sql_attempts']) == 2
    assert saved['sql_attempts'][-1]['parent_execution_id'] == saved['sql_attempts'][0]['execution_id']
import json


def test_advanced_explanation_is_saved_reviewed_and_restored(flow):
    document = start(flow, 'advanced')
    notes = '중복: 고유 가입자 수와 조인 전후 수를 비교하고 재도전은 EXISTS로 확인할 계획입니다. 기간: 시작 직전·시작·종료 직전·종료 시각과 가입일 7일 경계 표본을 대조할 계획입니다. 분모/NULL: 미도전자 포함 고유 가입자와 완료자 수를 따로 계산하고 0명 분모에서 NULL인지 확인할 계획입니다.'
    checks = [{'id':key,'status':'met','reason':'공개 조건에 연결된 구체적인 검산 계획'} for key in ('duplicates','period','denominator')]
    flow[0].provider.review.return_value = {'state':'completed','text':json.dumps({'checks':checks}),'model':'gemma-fixture'}
    first = handle(flow, document, 'sqlrun', block(reference_sql(document['task']))+'\n'+notes)
    assert first['session']['sql_attempts'][-1]['verification_notes']==notes
    result = handle(flow, document, 'submit')
    assert result['submission']['completed']
    assert all(c['status']=='충족' for c in result['session']['evaluations'][-1]['result']['criteria'])
    flow[0].provider.review.assert_called_once()
    restored = DiscordTrainingService(DiscordStore(flow[0].store.dsn),flow[0].engine,Mock(),flow[0].settings).resume(flow[1],'10',document['session_id'])
    assert restored['session']['sql_attempts'][-1]['verification_notes']==notes
    assert any(c['title']=='검산 방법 설명' and notes in c['description'] for c in result['submission']['cards'])
