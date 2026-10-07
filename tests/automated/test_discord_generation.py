from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, Mock

import pytest

from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_generation import task_from_recipe, request
from da_agent.discord_generated_query import compile_conditions, GeneratedQueryEngine
from da_agent.adaptive_tasks import Recipe
from da_agent.errors import DomainError
from da_agent.evaluation_registry import task_key, QualityRegistry
from test_adaptive_tasks import bot_recipe


class MemoryStore:
    def __init__(self): self.docs={}; self.events={}; self.jobs={}; self.calls=0
    def claim_event(self,event,owner,*args,**kwargs): return deepcopy(self.events.get(event))
    def finish_event(self,event,response,*args): self.events[event]=deepcopy(response)
    def create(self,doc,event,response): self.docs[doc['session_id']]=deepcopy(doc); self.finish_event(event,response)
    def get(self,owner,sid):
        doc=self.docs[sid]
        if doc['owner_user_id']!=owner: raise DomainError('forbidden','자신의 기록만 사용하세요.',403)
        return deepcopy(doc)
    def list(self,owner,guild=None): return [deepcopy(d) for d in self.docs.values() if d['owner_user_id']==owner and (guild is None or d['guild_id']==guild)]
    @contextmanager
    def edit(self,owner,sid):
        doc=self.get(owner,sid)
        yield doc,None
        self.docs[sid]=deepcopy(doc)
    def reserve_call(self,owner,limit):
        if self.calls>=limit: raise DomainError('usage_limit','호출 한도',429)
        self.calls+=1
    def generation_job(self,owner,sid): self.get(owner,sid); return deepcopy(self.jobs.get(sid,{}))
    def save_generation_job(self,owner,sid,job): self.get(owner,sid); self.jobs[sid]=deepcopy(job)


def public(): return {'package_id':'discord-generated-test','dataset_id':'generated-data','plan_version':'adaptive-plan-v1',
    'description':'공개 과제 설명','data_complete_before':'2026-09-02T00:00:00+09:00','semantic_signature':{}}


def test_practice_dispatch_preserves_text_generation_for_both_modes(monkeypatch):
    service=DiscordTrainingService(MemoryStore(),NS(),Mock(),NS())
    generated=service.start('u','g','c','analysis-event',text='계정 행동 비교',practice='analysis')
    assert generated['practice']=='analysis' and generated['generation']['original_message']=='계정 행동 비교'
    legacy=Mock(return_value={'session_id':'sql'})
    monkeypatch.setattr(service,'_start_legacy',legacy)
    sql = service.start('u','g','c','sql-event',text='게임 재화 분석',practice='sql')
    assert sql['practice']=='sql' and sql['generation']['original_message']=='게임 재화 분석'
    legacy.assert_not_called()
    with pytest.raises(DomainError):
        service.start('u','g','c','bad-mode',text='계정 비교',practice='other')


@pytest.fixture
def generated_service(monkeypatch,tmp_path):
    store=MemoryStore()
    provider=Mock(model='gemma-test')
    aligned={'aligned':True,'issues':[],'quality_dimensions':{k:'pass' for k in ('business_context','evidence_sufficiency','difficulty_fit','evaluation_alignment')}}
    provider.review.side_effect=[{'state':'completed','text':json.dumps(bot_recipe())},
                               {'state':'completed','text':json.dumps(aligned)}]
    settings=NS(admin_dsn='postgresql://generator:pass@localhost/discord_data',
        learner_dsn='postgresql://reader:pass@localhost/discord_data',generation_directory=str(tmp_path),daily_call_limit=30)
    service=DiscordTrainingService(store,NS(runner=Mock()),provider,settings)
    session=service.start('owner','guild','channel','event',text='반복 행동 계정의 정상 반례를 비교하고 싶어',practice='analysis')
    store.save_generation_job('owner',session['session_id'],{'source_case':{'topic':'반복 행동','sources':[]}})
    connection=MagicMock(); connection.__enter__.return_value.execute.return_value.fetchone.return_value=(False,False,False,False)
    monkeypatch.setattr('psycopg.connect',lambda *a,**k:connection)
    stage=Mock(return_value=(NS(schema_name='pkg_test'),public(),{'sql':'private answer'}))
    monkeypatch.setattr('da_agent.discord_generation.adaptive.stage_adaptive',stage)
    for name in ('grant','publish','revoke'): monkeypatch.setattr('da_agent.discord_generation.'+name,Mock())
    return service,session,stage


def test_request_rejects_empty_and_preserves_goal_and_difficulty():
    for text in ('',' ', 'x'*4001):
        with pytest.raises(DomainError): request(text,'id','beginner')
    value=request(' 재화 경제 분석 ','id','advanced')
    assert value.message=='재화 경제 분석' and value.difficulty=='advanced' and value.data_mode=='adaptive'


def test_generation_reuses_schema_and_preserves_public_contract(generated_service):
    service,session,stage=generated_service
    doc=service.generate('owner',session['session_id'])
    assert doc['state']=='analysis',doc
    assert doc['generation']['status']=='ready'
    assert set(doc['task']['schema'])=={'accounts','activity_daily'}
    assert doc['task']['rubric']['weights_total']==100
    assert 'tutorial_attempts' not in doc['task']['schema']
    assert 'private answer' not in json.dumps(doc)
    assert stage.call_args.kwargs['admin_dsn']==service.settings.admin_dsn
    assert stage.call_args.kwargs['learner_role']=='reader'
    assert service.generate('owner',session['session_id'])==doc
    assert service.store.calls==2
    assert service.quality_registry.check(doc['task'],'gemma-test')['status']!='eligible'


def test_cancel_during_model_call_never_stages_or_grants(generated_service):
    service,session,stage=generated_service
    def cancel(messages):
        service.handle('owner',session['session_id'],'cancel','end')
        return {'state':'completed','text':json.dumps(bot_recipe())}
    service.provider.review.side_effect=cancel
    doc=service.generate('owner',session['session_id'])
    assert doc['generation']['status']=='cancelled'
    stage.assert_not_called()


def test_failed_generation_resume_is_read_only_and_retry_uses_fixed_recipe(generated_service):
    service,session,stage=generated_service
    stage.side_effect=DomainError('validation_failed','실제 DB 검산 실패')
    failed=service.generate('owner',session['session_id'])
    assert failed['generation']['status']=='failed'
    before=service.store.calls
    assert service.resume('owner','guild',session['session_id'])['session']['state']=='failed'
    assert service.store.calls==before
    stage.side_effect=None
    ready=service.handle('owner',session['session_id'],'retry','retry')['session']
    assert ready['state']=='analysis' and service.store.calls==before
    assert ready['generation']['error_code'] is None


def test_clarification_and_owner_scoped_answer(generated_service):
    service,session,stage=generated_service
    recipe=bot_recipe(); recipe.update(status='clarify',questions=['어떤 관측 기간인가요?'])
    service.provider.review.side_effect=[{'state':'completed','text':json.dumps(recipe)}]
    doc=service.generate('owner',session['session_id'])
    assert doc['generation']['status']=='needs_clarification'
    assert doc['pending_question']['kind']=='generation_conditions'
    with pytest.raises(DomainError): service.handle('other',session['session_id'],'foreign','answer','9월1일')
    assert service.store.calls==1


def test_clarification_answer_revises_saved_request_without_changing_original(generated_service):
    service,session,stage=generated_service
    sid=session['session_id']
    with service.store.edit('owner',sid) as (doc,_):
        doc['generation']['status']='needs_clarification'
        doc['generation']['questions']=['기간은 언제인가요?']
        doc['state']='needs_clarification'
    captured=[]
    def generate(owner,sid,retry=False):
        captured.append(service.store.get(owner,sid)['generation']['message'])
        with service.store.edit(owner,sid) as (doc,_): doc['generation']['status']='failed'; doc['state']='failed'
        return doc
    service.generate=generate
    value=service.handle('owner',sid,'answer','answer','2026-09-01 하루')['session']
    assert value['generation']['revision']==1
    assert '추가 답변: 2026-09-01 하루' in captured[0]
    assert value['generation']['original_message']==session['generation']['original_message']
    assert 'source_case' not in service.store.generation_job('owner',sid)
    # Replaying the original start returns the same revised session.
    assert service.start('owner','guild','channel','event',text=session['generation']['original_message'],practice='analysis')['session_id']==sid


def test_duplicate_event_conflicting_request_is_rejected(generated_service):
    service,session,stage=generated_service
    with pytest.raises(DomainError,match='다른 출제'):
        service.start('owner','guild','channel','event',text='다른 요청',practice='analysis')
    stage.assert_not_called()


def test_call_limit_prevents_model_and_dataset(generated_service):
    service,session,stage=generated_service
    with service.store.edit('owner',session['session_id']) as (doc,_): doc['generation']['planning_calls']=8
    result=service.generate('owner',session['session_id'])
    assert result['generation']['error_code']=='planning_limit'
    service.provider.review.assert_not_called()
    stage.assert_not_called()


def test_missing_search_configuration_is_explicit_and_no_fallback(generated_service):
    service,session,stage=generated_service
    service.store.jobs.clear()
    service.provider.research.return_value={'state':'error','reason':'research_unavailable'}
    result=service.generate('owner',session['session_id'])
    assert result['generation']['error_code']=='research_unavailable'
    assert '출제 방식' in result['generation']['error']
    service.provider.review.assert_not_called()
    stage.assert_not_called()


def test_gemma_generates_directly_without_research_or_selection(generated_service):
    from da_agent.discord_presentation import task_intro
    service, session, stage = generated_service
    service.provider.generation_mode = 'synthetic'
    service.store.jobs.clear()
    doc = service.generate('owner', session['session_id'])
    assert doc['generation']['status'] == 'ready', doc
    service.provider.research.assert_not_called()
    service.provider.select_case.assert_not_called()
    assert service.store.calls == 2
    source = doc['task']['source_case']
    assert source['version'] == 'discord-synthetic-v1'
    assert source['sources'] == [] and source['searched_at'] is None
    assert source['topic'] == doc['task']['topic']
    design = service.provider.review.call_args_list[0].args[0]
    assert '실제 검색은 하지 않습니다' in design[0]['content']
    assert json.loads(design[1]['content'])['source_case'] is None
    assert '가상 분석 문제' in task_intro(doc)
    before = service.store.calls
    assert service.generate('owner', session['session_id']) == doc
    assert service.store.calls == before


def test_json_diagnostic_is_saved_only_in_private_job(generated_service):
    service, session, stage = generated_service
    diagnostic = {'kind':'json_syntax', 'line':12, 'column':5, 'position':100, 'diagnostic_id':'private-diagnostic-id'}
    service.provider.review.side_effect = None
    service.provider.review.return_value = {'state':'error','reason':'provider_invalid_json','text':'','json_diagnostic':diagnostic}
    doc = service.generate('owner', session['session_id'])
    assert doc['generation']['error_code'] == 'provider_invalid_json'
    job = service.store.generation_job('owner', session['session_id'])
    assert job['calls'][0]['json_diagnostic'] == diagnostic
    assert 'private-diagnostic-id' not in json.dumps(doc)
    stage.assert_not_called()


def test_gemma_fixed_recipe_retry_does_not_redesign(generated_service):
    service, session, stage = generated_service
    service.provider.generation_mode = 'synthetic'
    service.store.jobs.clear()
    stage.side_effect = DomainError('validation_failed', 'DB 검산 실패')
    assert service.generate('owner', session['session_id'])['generation']['status'] == 'failed'
    before = service.store.calls
    stage.side_effect = None
    assert service.handle('owner', session['session_id'], 'gemma-retry', 'retry')['session']['state'] == 'analysis'
    assert service.store.calls == before


def test_manual_retry_reuses_rejected_recipe_and_current_validation_feedback(generated_service):
    service, session, stage = generated_service
    bad = bot_recipe(); bad['tables'][0]['groups'][0]['count'] = 501
    bad_response = {'state':'completed','text':json.dumps(bad)}
    service.provider.review.side_effect = [bad_response]*3
    failed = service.generate('owner',session['session_id'])
    assert failed['generation']['status'] == 'failed'
    job = service.store.generation_job('owner',session['session_id'])
    assert len(job['rejected_designs']) == 3
    assert 'rejected_designs' not in json.dumps(failed)
    aligned = {'aligned':True,'issues':[],'quality_dimensions':{k:'pass' for k in ('business_context','evidence_sufficiency','difficulty_fit','evaluation_alignment')}}
    service.provider.review.side_effect = [{'state':'completed','text':json.dumps(bot_recipe())},
        {'state':'completed','text':json.dumps(aligned)}]
    ready = service.generate('owner',session['session_id'],retry=True)
    assert ready['generation']['status'] == 'ready'
    messages = service.provider.review.call_args_list[3].args[0]
    assert messages[-2]['role'] == 'assistant'
    assert json.loads(messages[-2]['content']) == json.loads(bad_response['text'])
    assert 'count' in messages[-1]['content'] and '500' in messages[-1]['content']
    assert service.store.calls == 5


def task(): return task_from_recipe(Recipe.model_validate(bot_recipe()),public(),None,'independent')


def test_derived_grouping_key_preserves_only_actual_source_fk():
    from da_agent.adaptive_tasks import generate_rows
    from da_agent.analytical_metrics import reference, foreign_key_target
    value = bot_recipe()
    value['tables'].append({'name':'account_summary','grain':'계정별 1행','derived_from':'activity_daily',
        'group_by':['account_id'],'unique_keys':[['account_id']], 'columns':[
            {'name':'summary_id','description':'요약 행 ID','generator':{'kind':'id'}},
            {'name':'account_id','description':'원본 계정 FK','generator':{'kind':'group_key','source_column':'account_id','value_type':'integer'}},
            {'name':'mean_actions','description':'계정별 평균 활동량','generator':{'kind':'aggregate','source_column':'actions','operation':'avg','value_type':'number'}}]})
    metric = next(m for m in value['metrics'] if m['name']=='platform_actions')
    metric.update(table='account_summary',column='mean_actions')
    recipe = Recipe.model_validate(value)
    tables = {t.name:t for t in recipe.tables}
    assert foreign_key_target('account_summary','account_id',tables)=='accounts'
    assert foreign_key_target('account_summary','mean_actions',tables) is None
    rows = generate_rows(recipe,2)
    result, query = reference(next(m for m in recipe.metrics if m.name=='platform_actions'),tables,rows)
    assert len(result['rows'])==2 and 'JOIN "accounts"' in query
    doc_task = task_from_recipe(recipe,public(),None,'independent')
    assert {'table':'account_summary','column':'account_id','target_table':'accounts','target_column':'account_id'} in doc_task['relationships']
    compiled,_ = compile_conditions({'table':'account_summary','operation':'avg','column':'mean_actions',
        'joins':[{'table':'accounts','source_column':'account_id'}],'group_by':['accounts.platform']},doc_task)
    assert compiled.replace('AS "value"','AS "platform_actions"')==query
    with pytest.raises(ValueError):
        compile_conditions({'table':'account_summary','operation':'count','joins':[{'table':'accounts','source_column':'mean_actions'}]},doc_task)


def test_generic_aggregate_joins_only_public_fk_and_compiles_no_model_sql():
    value=task()
    conditions={'table':'activity_daily','operation':'avg','column':'actions',
        'joins':[{'table':'accounts','source_column':'account_id'}],'group_by':['accounts.platform']}
    sql,tables=compile_conditions(conditions,value)
    assert 'JOIN "accounts"' in sql and 'AVG' in sql and tables==['activity_daily','accounts']
    for bad in ({**conditions,'table':'pg_roles'}, {**conditions,'column':'private_secret'},
                {**conditions,'joins':[{'table':'accounts','source_column':'actions'}]},
                {**conditions,'sql':'DROP TABLE accounts'}):
        with pytest.raises((ValueError,TypeError)): compile_conditions(bad,value)


def test_generic_ratio_requires_numerator_and_no_filter_injection():
    value=task()
    with pytest.raises(ValueError): compile_conditions({'table':'activity_daily','operation':'ratio'},value)
    sql,_=compile_conditions({'table':'accounts','operation':'count','conditions':[
        {'column':'platform','operator':'eq','value':"pc'; DROP TABLE accounts; --"}]},value)
    from da_agent.sql_runner import check_query
    check_query(sql,['accounts'])
    assert "pc'';" in sql


def test_generated_query_captures_full_evidence_and_rejects_changed_plan():
    provider=Mock(); provider.review.return_value={'state':'completed','text':json.dumps({'state':'ready',
        'conditions':{'table':'accounts','operation':'count'}})}
    runner=Mock(); result={'status':'success','execution_id':'e','columns':[{'name':'value'}],'rows':[[60]],'result_complete':True}
    runner.execute.return_value=result
    engine=GeneratedQueryEngine(provider,runner,NS())
    plan=engine.resolve('계정 수를 세어줘',task())
    runner.get.return_value={'sql':plan['sql'],'result':result}
    assert engine.execute('session','pkg',plan,task())['state']=='success'
    changed={**plan,'sql':'SELECT * FROM pg_roles'}
    assert engine.execute('session','pkg',changed,task())['state']=='error'
    assert runner.execute.call_count==1


@pytest.mark.parametrize('key',['accepted_limits','valid_paths','quality_information','title'])
def test_quality_profile_changes_with_all_public_evaluation_definitions(key):
    value=task(); before=task_key(value)
    value[key]=['changed']
    assert task_key(value)!=before
