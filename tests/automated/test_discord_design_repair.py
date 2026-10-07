"""Regression of repeated corrections, row units and pre-send quota accounting."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest

from da_agent import adaptive_tasks as adaptive
from da_agent.discord_design import compact_schema, planning_messages, repair_messages, relational_context
from da_agent.discord_generation import failure_message, request, status_message
from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.discord_api_budget import ApiBudget
from da_agent.discord_service import DiscordTrainingService
from da_agent.errors import DomainError
from test_adaptive_tasks import bot_recipe
from test_discord_generation import MemoryStore, generated_service


def test_repeated_errors_keep_one_latest_draft_and_all_private_history(generated_service):
    service, session, stage = generated_service
    bad = bot_recipe(); bad['metrics'][-1]['table'] = 'accounts'
    bad['metrics'][-1]['joins'] = [{'table':'activity_daily','source_column':'account_id'}]
    service.provider.review.side_effect = [{'state':'completed','text':json.dumps(bad)}] * 3
    failed = service.generate('owner',session['session_id'])
    assert failed['generation']['status'] == 'failed'
    calls = service.provider.review.call_args_list
    assert [len(c.args[0]) for c in calls] == [2,4,4]
    assert all(sum(m['role']=='assistant' for m in c.args[0])==1 for c in calls[1:])
    feedback = json.loads(calls[-1].args[0][-1]['content'])
    assert '같은 오류가 반복' in feedback['instruction']
    assert feedback['relationships']['allowed_joins'] == [{'table':'activity_daily','source_column':'account_id',
        'target_table':'accounts','target_column':'account_id'}]
    assert any(i.get('metric')=='platform_actions' and i['location']==['metrics',3] for i in feedback['issues'])
    assert '로그 행 비율로 바꾸지' in feedback['instruction']
    assert len(service.store.generation_job('owner',session['session_id'])['rejected_designs'])==3
    assert 'allowed_joins' not in json.dumps(failed)
    stage.assert_not_called()


def test_repair_feedback_is_specific_and_does_not_accumulate():
    data=request('계정 활동 비교','sid','intermediate')
    base=planning_messages(data,[])
    bad=bot_recipe(); bad['tables'][0]['groups'][0]['count']=2001
    m=repair_messages(base,json.dumps(bad),[{'location':['tables',0,'groups',0,'count'],'type':'less_than_equal','message':'count <= 2000'}])
    feedback=json.loads(m[-1]['content'])
    assert '2000' in feedback['instruction']
    assert 'ratio의 명시적인 분자' not in feedback['instruction']
    assert base==planning_messages(data,[])
    assert json.loads(m[-2]['content'])==bad


def test_retry_discards_previous_rate_limit_time_when_design_fails(generated_service):
    service,session,stage=generated_service
    doc=service.store.docs[session['session_id']]
    doc['generation'].update(status='failed',error_code='api_rate_limited',retry_at='2026-10-07T01:01:00+00:00')
    bad=bot_recipe(); bad['metrics'][-1]['table']='accounts'
    bad['metrics'][-1]['joins']=[{'table':'activity_daily','source_column':'account_id'}]
    service.provider.review.side_effect=[{'state':'completed','text':json.dumps(bad)}]*3
    failed=service.generate('owner',session['session_id'],retry=True)
    assert failed['generation']['status']=='failed'
    assert failed['generation']['error_code']!='api_rate_limited'
    assert 'retry_at' not in failed['generation']
    assert '재시도 가능 시각' not in status_message(failed)
    # Legacy records can still contain the old field; don't render a false time.
    failed['generation']['retry_at']='2026-10-07T01:01:00+00:00'
    assert '재시도 가능 시각' not in status_message(failed)
    stage.assert_not_called()


def test_relation_context_preserves_derived_fk_and_account_ratio_unit():
    raw=bot_recipe()
    raw['tables'][1]['grain']='계정별 여러 활동 로그'
    raw['tables'][1]['unique_keys']=[]
    raw['tables'].append({'name':'account_summary','grain':'계정별 한 행 요약','derived_from':'activity_daily',
        'group_by':['account_id'],'unique_keys':[['account_id']], 'columns':[
            {'name':'summary_id','description':'요약 식별','generator':{'kind':'id'}},
            {'name':'account_id','description':'계정 참조','generator':{'kind':'group_key','source_column':'account_id','value_type':'integer'}},
            {'name':'actions','description':'행동 합계','generator':{'kind':'aggregate','source_column':'actions','operation':'sum','value_type':'integer'}}]})
    context,issues=relational_context(json.dumps(raw))
    assert any(r['table']=='account_summary' and r['target_table']=='accounts' for r in context['allowed_joins'])
    assert not any(r['table']=='accounts' for r in context['allowed_joins'])
    assert context['tables'][-1]['unique_keys']==[['account_id']]
    # Two events for one user and one for another: event and user rates differ.
    metric=adaptive.Metric(name='rate',table='account_summary',operation='ratio',conditions=[{'column':'actions','operator':'gt','value':100}])
    from da_agent.analytical_metrics import reference
    tables={t.name:t for t in map(adaptive.Table.model_validate,raw['tables'])}
    result,_=reference(metric,tables, {'account_summary':[{'summary_id':1,'account_id':1,'actions':160},{'summary_id':2,'account_id':2,'actions':20}]})
    assert result['rows']==[[.5]]
    event_metric=metric.model_copy(update={'table':'activity_daily'})
    result,_=reference(event_metric,tables, {'activity_daily':[{'activity_id':1,'account_id':1,'actions':160},{'activity_id':2,'account_id':1,'actions':160},{'activity_id':3,'account_id':2,'actions':20}]})
    assert result['rows'][0][0]==pytest.approx(2/3)


def test_compact_schema_keeps_constraints_and_property_names():
    schema=adaptive.recipe_response_schema(); compact=compact_schema(schema)
    assert compact['title']=='Recipe'
    assert set(compact['properties'])==set(schema['properties'])
    assert compact['properties']['title']['minLength']==1
    assert compact['properties']['description']['maxLength']==3000
    assert compact['$defs']['Group']['properties']['count']['maximum']==2000
    assert compact['required']==schema['required']
    for name in schema['$defs']:
        assert set(compact['$defs'][name].get('properties',{}))==set(schema['$defs'][name].get('properties',{}))


@pytest.mark.parametrize('level,need,absent', [('beginner','초급:','고급:'),('intermediate','중급:','초급:'),('advanced','고급:','중급:')])
def test_synthetic_prompt_is_scoped_without_web_search_conflict(level,need,absent):
    message=planning_messages(request('게임 업무 분석','sid',level),[])
    assert need in message[0]['content'] and absent not in message[0]['content']
    assert '검색으로 선정' not in message[0]['content']
    assert json.loads(message[1]['content'])['schema']['properties']['difficulty']['enum']==[level]
    web=adaptive.planning_messages(request('게임 업무 분석','sid',level),[])
    assert '검색으로 선정' in web[0]['content']


def provider(tmp_path, respond, *, budget=False):
    key=tmp_path/'key'; key.write_text('test-key')
    return DiscordGemmaProvider(key_file=key,http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        budget_directory=tmp_path/'budget' if budget else None)


def response():
    return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{}'}]}}]})


def test_oversize_never_sends_or_charges_and_reports_estimate(tmp_path):
    sent=[]; charge=Mock()
    p=provider(tmp_path,lambda req:sent.append(req) or response())
    result=p.review([{'role':'user','content':'가'*20000}],before_send=charge)
    assert result['reason']=='api_input_budget'
    assert not sent and not charge.called
    assert result['input_diagnostic']['sent'] is False
    assert result['input_diagnostic']['estimated_input_tokens']>14000


def test_service_does_not_consume_call_limit_for_local_oversize(tmp_path):
    sent=[]; p=provider(tmp_path,lambda req:sent.append(req) or response())
    store=MemoryStore(); service=DiscordTrainingService(store,NS(),p,NS(daily_call_limit=30))
    doc=service.start('u','g','c','e',text='게임 분석',practice='analysis')
    # Recent signatures are private history, not user text; emulate an oversized envelope.
    other=deepcopy(doc); other.update(session_id='other',task={'semantic_signature':{'large':'가'*20000}})
    other['generation']['status']='ready'; store.docs['other']=other
    result=service.generate('u',doc['session_id'])
    assert result['generation']['error_code']=='api_input_budget'
    assert result['generation']['planning_calls']==0 and store.calls==0 and not sent
    assert '반복하지' in status_message(result)


def test_shared_budget_and_429_wait_do_not_send_again(tmp_path):
    sent=[]; charged=[]
    def limited(req):
        sent.append(req)
        if req.url.path.endswith(':countTokens'):
            return httpx.Response(200,json={'totalTokens':100})
        return httpx.Response(429,json={'error':{'details':[{'retryDelay':'12s'}]}})
    p=provider(tmp_path,limited,budget=True)
    first=p.review([{'role':'user','content':'x'}],before_send=lambda:charged.append(1))
    assert first['reason']=='api_rate_limited' and len(charged)==1
    second=p.review([{'role':'user','content':'x'}],before_send=lambda:charged.append(1))
    assert second['reason']=='api_rate_limited' and len(charged)==1
    assert sum(req.url.path.endswith(':generateContent') for req in sent)==1
    assert sum(req.url.path.endswith(':countTokens') for req in sent)==2
    assert second['input_diagnostic']['retry_after_seconds']>0
    assert '초 뒤' in failure_message(second['reason'],second['input_diagnostic'])


def test_daily_limit_and_cancel_release_budget_without_generation(tmp_path):
    sent=[]; p=provider(tmp_path,lambda req:sent.append(req) or response(),budget=True)
    def exhausted(): raise DomainError('usage_limit','오늘 한도')
    assert p.review([{'role':'user','content':'x'}],before_send=exhausted)['reason']=='usage_limit'
    def cancelled(): raise DomainError('cancelled','취소')
    assert p.review([{'role':'user','content':'x'}],before_send=cancelled)['reason']=='cancelled'
    import sqlite3
    with sqlite3.connect(p.http.budget.path) as conn:
        assert conn.execute('SELECT count(*) FROM calls').fetchone()[0]==0
    assert len(sent)==2 and all(req.url.path.endswith(':countTokens') for req in sent)


def test_missing_key_never_charges_and_callback_is_cleared(tmp_path):
    p=DiscordGemmaProvider(key_file=tmp_path/'missing',http_client=httpx.Client(transport=httpx.MockTransport(lambda req:response())))
    charge=Mock(); result=p.review([{'role':'user','content':'x'}],before_send=charge)
    assert result['reason']=='api_key_missing' and not charge.called
    assert p.http.before_send is None


def test_transport_attempt_is_counted_even_when_response_times_out(tmp_path):
    charge=Mock()
    def timeout(req): raise httpx.ReadTimeout('fixture timeout',request=req)
    p=provider(tmp_path,timeout)
    result=p.review([{'role':'user','content':'x'}],before_send=charge)
    assert result['reason']=='api_timeout' and charge.call_count==1
    assert result['input_diagnostic']['sent'] is True


def test_sdk_does_not_offer_blind_retry_for_oversize_but_keeps_rate_retry(tmp_path,monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from da_agent.discord_bot import create_client
    monkeypatch.setenv('DISCORD_RESPONSE_DIRECTORY',str(tmp_path/'responses'))
    async def check():
        client=create_client(Mock(),NS(message_content=False,guild_ids=(10,)))
        channel=NS(send=AsyncMock())
        for code in ('api_input_budget','planning_limit','api_key_missing'):
            await client.da_transport.gateway.generation_controls(channel,{'session_id':'sid','generation':{'status':'failed','error_code':code}})
        assert channel.send.call_count==0
        await client.da_transport.gateway.generation_controls(channel,{'session_id':'sid','generation':{'status':'failed','error_code':'api_rate_limited'}})
        assert channel.send.call_count==1
        assert channel.send.call_args.kwargs['view'].children[0].custom_id=='generation-retry:sid'
        await client.close()
    asyncio.run(check())


def test_rolling_budget_retains_compatibility_and_recovers_after_window(tmp_path):
    a=ApiBudget(tmp_path/'budget.sqlite',limit=100,window=65)
    first,delay=a.reserve('scope',80,now=100); assert first and delay==0
    a.actual(first,80,now=100)
    assert a.reserve('scope',30,now=110)==(None,55)
    assert a.reserve('scope',101,now=110)==(None,None)
    assert ApiBudget(a.path,limit=100,window=65).reserve('scope',30,now=165)[0]


def test_existing_pending_volume_preserves_usage_and_releases_only_unsent(tmp_path):
    import sqlite3
    path=tmp_path/'existing.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE calls (id TEXT PRIMARY KEY,scope TEXT,at REAL,tokens INTEGER,pending INTEGER NOT NULL DEFAULT 0)')
        conn.execute('INSERT INTO calls VALUES (?,?,?,?,?)',('existing','scope',100,70,0))
    budget=ApiBudget(path,limit=100,window=65)
    assert budget.reserve('scope',40,now=110)==(None,55)
    new,_=budget.reserve('scope',20,now=110)
    assert new
    budget.release(new)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT id,tokens,pending FROM calls').fetchall()==[('existing',70,0)]


def test_exact_token_count_can_block_generation_without_charging(tmp_path):
    sent=[]; charge=Mock()
    def counted(req):
        sent.append(req)
        return httpx.Response(200,json={'totalTokens':15000})
    p=provider(tmp_path,counted,budget=True)
    result=p.review([{'role':'user','content':'small input'}],before_send=charge)
    assert result['reason']=='api_input_budget' and not charge.called
    assert len(sent)==1 and sent[0].url.path.endswith(':countTokens')
    assert result['input_diagnostic']['sent'] is False
    assert result['input_diagnostic']['input_budget_basis']=='countTokens'


def test_timeout_marks_reservation_completed_without_releasing_usage(tmp_path):
    import sqlite3
    charge=Mock()
    def timeout(req):
        if req.url.path.endswith(':countTokens'):
            return httpx.Response(200,json={'totalTokens':100})
        raise httpx.ReadTimeout('fixture timeout',request=req)
    p=provider(tmp_path,timeout,budget=True)
    assert p.review([{'role':'user','content':'x'}],before_send=charge)['reason']=='api_timeout'
    assert charge.call_count==1
    with sqlite3.connect(p.http.budget.path) as conn:
        assert conn.execute('SELECT tokens,pending FROM calls').fetchall()==[(356,0)]


def test_advanced_prompt_preserves_judgment_contract_and_retry_time(tmp_path):
    text=planning_messages(request('게임 업무 분석','sid','advanced'),[])[0]['content']
    assert all(rule in text for rule in ('judgment','control_columns','conditional_comparison','non_identifiable'))
    assert '모집단에 conditions를 추가 적용한 부분집합' in text
    p=provider(tmp_path,lambda req:httpx.Response(429,json={'error':{'details':[{'retryDelay':'90s'}]}}))
    result=p.review([{'role':'user','content':'x'}])
    assert result['reason']=='api_rate_limited' and result['retry_at']
    message=status_message({'generation':{'status':'failed','error_code':'api_rate_limited','retry_at':result['retry_at']}})
    assert 'KST' in message


def test_bounded_wait_keeps_accounting_after_capacity_becomes_available(tmp_path,monkeypatch):
    from da_agent import discord_provider
    events=[]
    def respond(req):
        events.append(req.url.path.rsplit(':',1)[-1])
        return httpx.Response(200,json={'totalTokens':100}) if req.url.path.endswith(':countTokens') else response()
    p=provider(tmp_path,respond,budget=True)
    p.http.budget_wait_seconds=130
    p.http.budget.reserve=Mock(side_effect=[(None,2),('reservation',0)])
    monkeypatch.setattr(discord_provider.time,'monotonic',lambda:0)
    monkeypatch.setattr(discord_provider.time,'sleep',lambda delay:events.append(('wait',delay)))
    result=p.review([{'role':'user','content':'x'}],before_send=lambda:events.append('charge'))
    assert result['state']=='completed'
    assert events==['countTokens',('wait',2.1),'charge','generateContent']


def test_db_usage_and_session_counter_roll_back_together():
    import os
    import uuid
    from da_agent.discord_store import DiscordStore
    dsn=os.getenv('DISCORD_TEST_RECORDS_DSN')
    if not dsn: pytest.skip('Requires explicit disposable Discord test records DB')
    store=DiscordStore(dsn); store.initialize()
    owner='repair-'+uuid.uuid4().hex
    service=DiscordTrainingService(store,NS(),Mock(),NS(daily_call_limit=1))
    doc=service.start(owner,'test-guild','test-channel',uuid.uuid4().hex,text='계정 행동 비교',practice='analysis')
    sid=doc['session_id']
    with pytest.raises(RuntimeError):
        with store.edit(owner,sid) as (current,conn):
            store.reserve_call(owner,1,conn=conn)
            current['generation']['planning_calls']=1
            raise RuntimeError('simulated transaction failure')
    assert store.get(owner,sid)['generation']['planning_calls']==0
    with store.connect() as conn:
        assert conn.execute('SELECT calls FROM discord_records.usage WHERE owner_id=%s',(owner,)).fetchone() is None
    with store.edit(owner,sid) as (current,conn):
        assert store.reserve_call(owner,1,conn=conn)==1
        current['generation']['planning_calls']=1
    with pytest.raises(DomainError):
        with store.edit(owner,sid) as (current,conn):
            store.reserve_call(owner,1,conn=conn)
            current['generation']['planning_calls']=2
    assert store.get(owner,sid)['generation']['planning_calls']==1
