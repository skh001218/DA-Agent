"""Bounded recipes, independent aggregates and isolated actual-DB generation."""
import copy
import json
import os
import uuid

import pytest
from pydantic import ValidationError

from da_agent.adaptive_tasks import Recipe, generate_rows, metric_reference, validate_alignment
from da_agent.errors import DomainError


def bot_recipe():
    return {
        'status':'ready','reason':'요청한 비정상 이용자 탐지의 근거와 오탐을 검토',
        'topic':'비정상 이용자','goal':'비정상 이용자 의심 패턴과 정상 반례 비교',
        'title':'반복 행동 이용자 조사','description':'운영팀은 반복 행동을 하는 계정의 조사를 요청했습니다. 2026-09-01 하루 활동 요약으로 의심 기준을 정하고, 활동량이 많은 정상 이용자 가능성과 구분하세요. 확정 제재를 위한 추가 근거도 제안하세요.',
        'difficulty':'intermediate','difficulty_reason':'의심 기준과 비교 방법을 학습자가 결정',
        'task_kind':'investigation','completion_conditions':['의심 행동과 정상 반례를 비교','실제 실행 근거와 오탐 가능성 설명'],
        'limitations':['하루 집계만으로 부정행위를 확정할 수 없음'],
        'rubric':'집계 단위와 비교·정상 반례·불확실성을 평가하며 비공개 생성 그룹 맞히기는 요구하지 않음',
        'tables':[
            {'name':'accounts','grain':'계정별 1행','columns':[
                {'name':'account_id','description':'계정 식별자','generator':{'kind':'id'}},
                {'name':'platform','description':'플랫폼','generator':{'kind':'category','values':['pc','mobile']}}],
             'groups':[{'name':'general','count':40},{'name':'regular','count':10},{'name':'heavy','count':10}]},
            {'name':'activity_daily','grain':'계정별 하루 활동 요약','unique_keys':[['account_id']],'columns':[
                {'name':'activity_id','description':'활동 요약 식별자','generator':{'kind':'id'}},
                {'name':'account_id','description':'accounts의 계정 기본키 참조','generator':{'kind':'foreign_key','table':'accounts'}},
                {'name':'actions','description':'하루 수행 행동 수','generator':{'kind':'integer','minimum':50,'maximum':200}},
                {'name':'interval_cv','description':'행동 간격의 변동계수','generator':{'kind':'number','minimum':0.5,'maximum':1.5}}],
             'groups':[
                {'name':'general','count':40,'overrides':{'account_id':{'kind':'foreign_key','table':'accounts','group':'general'}}},
                {'name':'regular','count':10,'overrides':{'account_id':{'kind':'foreign_key','table':'accounts','group':'regular'},'actions':{'kind':'integer','minimum':1000,'maximum':2000},'interval_cv':{'kind':'number','minimum':0.01,'maximum':0.04}}},
                {'name':'heavy','count':10,'overrides':{'account_id':{'kind':'foreign_key','table':'accounts','group':'heavy'},'actions':{'kind':'integer','minimum':1000,'maximum':2000}}}]}
        ],
        'metrics':[
            {'name':'activity_rows','table':'activity_daily','operation':'count','minimum':60},
            {'name':'regular_activity_rows','table':'activity_daily','operation':'count','conditions':[{'column':'actions','operator':'gte','value':1000},{'column':'interval_cv','operator':'lt','value':0.05}],'minimum':10},
            {'name':'mean_actions','table':'activity_daily','operation':'avg','column':'actions'}]
    }


def test_arbitrary_recipe_is_reproducible_and_preserves_relations():
    recipe=Recipe.model_validate(bot_recipe())
    rows=generate_rows(recipe,731)
    assert rows==generate_rows(recipe,731)
    assert rows!=generate_rows(recipe,732)
    assert {r['account_id'] for r in rows['activity_daily']}.issubset({r['account_id'] for r in rows['accounts']})
    expected, query=metric_reference(recipe,rows)
    assert expected['regular_activity_rows']==10
    assert 'activity_daily' in query and 'sessions' not in query
    assert all('group' not in r for table in rows.values() for r in table)


@pytest.mark.parametrize('change', ['injection','duplicate','bad_fk','overflow','numeric_type','private_label','missing_grain_key'])
def test_invalid_recipe_fails_before_db(change):
    value=bot_recipe()
    if change=='injection': value['tables'][0]['name']='accounts; DROP TABLE attempts'
    if change=='duplicate': value['tables'][0]['columns'][1]['name']='account_id'
    if change=='bad_fk': value['tables'][1]['columns'][1]['generator']['table']='private_users'
    if change=='overflow': value['tables'][0]['groups'][0]['count']=2001
    if change=='numeric_type': value['metrics'][1]['conditions'][0]['value']='1000'
    if change=='private_label': value['tables'][0]['columns'][1]['generator']['values']=['normal','bot']
    if change=='missing_grain_key': value['tables'][1]['unique_keys']=[]
    with pytest.raises(ValidationError): Recipe.model_validate(value)


def test_analysis_impossible_and_goal_mismatch_are_not_published():
    value=bot_recipe(); value['metrics'][1]['minimum']=999
    recipe=Recipe.model_validate(value)
    with pytest.raises(DomainError): metric_reference(recipe,generate_rows(recipe,2))
    for result in ({'aligned':False,'issues':['요청은 비정상 이용자인데 과제는 재접속']},{'aligned':True,'issues':['필수 데이터 부족']},{'aligned':'true','issues':[]}):
        with pytest.raises(DomainError): validate_alignment({'state':'completed','text':json.dumps(result)})


def test_entity_sequence_uses_actual_previous_event_time():
    value=bot_recipe()
    value['tables'][1]['columns'].append({'name':'event_at','description':'계정별 순서대로 생성한 이벤트 시각',
        'generator':{'kind':'timestamp_sequence','entity_column':'account_id','interval_column':'actions','start':'2026-09-01T00:00:00Z','end':'2026-09-02T00:00:00Z'}})
    recipe=Recipe.model_validate(value)
    from da_agent.adaptive_tasks import timestamp
    rows=generate_rows(recipe,52)['activity_daily']
    for account in {r['account_id'] for r in rows}:
        previous=timestamp('2026-09-01T00:00:00Z')
        for row in sorted([r for r in rows if r['account_id']==account],key=lambda r:r['event_at']):
            assert (timestamp(row['event_at'])-previous).total_seconds()==row['actions']
            previous=timestamp(row['event_at'])


def test_numeric_decimal_encoding_verifies_actual_average():
    from da_agent.evaluation import verify_evidence
    evidence={'saved_execution_id':'avg','result':{'status':'success','result_complete':True,
        'columns':[{'name':'mean_actions'}],'rows':[['587.8500000000000000']]}}
    assert verify_evidence(evidence,{'mean_actions':587.85})['status']=='verified'
    evidence['result']['rows']=[['NaN']]
    assert verify_evidence(evidence,{'mean_actions':587.85})['status']=='mismatch'


def test_db_adaptive_request_actual_tables_save_resume_and_fixed_retry(v2_db_client):
    client, provider=v2_db_client
    original_review=provider.review
    def review(messages):
        payload=json.loads(messages[1]['content'])
        if 'schema' in payload and 'request' in payload:
            provider.calls+=1
            return {'state':'completed','text':json.dumps(bot_recipe(),ensure_ascii=False),'model':'fixture'}
        if 'task' in payload and 'request' in payload:
            provider.calls+=1
            return {'state':'completed','text':'{"aligned":true,"issues":[]}','model':'fixture'}
        return original_review(messages)
    provider.review=review
    rid=str(uuid.uuid4())
    body={'contract_version':'request-v2','request_id':rid,'message':'게임 비정상 이용자 조사 문제','data_mode':'adaptive','domain':'auto'}
    assert client.post('/api/training/requests',json=body).status_code==200
    value=client.get('/api/training/requests/'+rid).json()
    assert value['status']=='ready', value
    aid=value['attempt_id']
    package=None
    try:
        attempt=client.get('/api/attempts/'+aid).json()
        assert attempt['problem']['plan_version']=='adaptive-plan-v1'
        assert '비정상' in attempt['problem']['goal'] and 'sessions' not in attempt['problem']['required_tables']
        assert '시험 과제' in attempt['problem']['evaluation_status']
        assert 'groups' not in json.dumps(attempt['problem'])
        result=client.post('/api/attempts/'+aid+'/execute',json={'sql':'SELECT count(*) AS activity_rows FROM activity_daily'}).json()
        assert result['status']=='success' and result['rows']==[[60]]
        saved=client.post('/api/attempts/'+aid+'/executions/save',json={'execution_id':result['execution_id'],'request_id':'save'}).json()
        resumed=client.get('/api/attempts/'+aid).json()
        assert saved['saved_execution_id'] in [x['saved_execution_id'] for x in resumed['saved_executions']]
        before=provider.calls
        assert client.post('/api/training/requests',json=body).json()['attempt_id']==aid
        assert provider.calls==before
        # Replay staged recipe after JSONB key reordering and reproduce actual DB validation.
        from da_agent.adaptive_tasks import stage_adaptive
        engine=client.app.state.training
        with engine.store.connect() as conn:
            fixed=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()['private']
        package,_,_=stage_adaptive(engine.catalog.root,fixed['package_id'],Recipe.model_validate(fixed['adaptive_recipe']),fixed['seed'],fixed['plan_id'],fixed['plan_revision'])
    finally:
        assert client.delete('/api/attempts/'+aid,headers={'Content-Type':'application/json'}).status_code==200
        if package:
            import psycopg
            from psycopg import sql
            from da_agent.package_validation import generator_dsn
            assert package.public['package_id'].startswith('generated-')
            with psycopg.connect(generator_dsn()) as conn:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(package.schema_name)))


@pytest.mark.parametrize('failure',['mismatch','labels'])
def test_db_adaptive_invalid_goal_or_label_plan_never_publishes(v2_db_client,failure):
    client,provider=v2_db_client
    def review(messages):
        data=json.loads(messages[1]['content'])
        provider.calls+=1
        if 'schema' in data:
            value=bot_recipe()
            if failure=='labels': value['labels_public']=True
            return {'state':'completed','text':json.dumps(value,ensure_ascii=False)}
        return {'state':'completed','text':'{"aligned":false,"issues":["필수 비교 자료 부족"]}'}
    provider.review=review
    body={'contract_version':'request-v2','request_id':str(uuid.uuid4()),'message':'비정상 이용자 조사 문제','data_mode':'adaptive'}
    assert client.post('/api/training/requests',json=body).status_code==200
    value=client.get('/api/training/requests/'+body['request_id']).json()
    assert value['status']=='failed' and value['attempt_id'] is None
    assert value['error_code']=='goal_mismatch' and value['generation_attempts']==0


def test_db_adaptive_cancel_during_model_never_generates_data(v2_db_client):
    client,provider=v2_db_client
    rid=str(uuid.uuid4())
    def review(messages):
        provider.calls+=1
        assert client.post('/api/training/requests/'+rid+'/cancel',json={}).status_code==200
        return {'state':'completed','text':json.dumps(bot_recipe(),ensure_ascii=False)}
    provider.review=review
    assert client.post('/api/training/requests',json={'contract_version':'request-v2','request_id':rid,'message':'비정상 이용자 조사','data_mode':'adaptive'}).status_code==200
    value=client.get('/api/training/requests/'+rid).json()
    assert value['status']=='cancelled' and value['attempt_id'] is None and value['generation_attempts']==0


from test_task_generation_v2 import v2_db_client
