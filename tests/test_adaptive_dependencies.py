"""Shared source/summary/time consistency and bounded semantic repair."""
import copy
import json
import uuid

import pytest
from pydantic import ValidationError

from da_agent.adaptive_tasks import Recipe, generate_rows, timestamp, metric_reference
from test_adaptive_tasks import bot_recipe
from test_task_generation_v2 import v2_db_client


def event_recipe(topic='구매'):
    value=bot_recipe()
    value.update(topic=topic,goal=topic+' 시간과 반복 시도 분석',title=topic+' 진행 분석',difficulty='advanced',
                 description=topic+' 이벤트를 계정과 대상별로 집계하고 관측 기간 내 성공하지 않은 이용자를 분리하세요.',
                 completion_conditions=['원본 이벤트의 시간과 성공 여부 분석','원본에서 산출한 요약과 비교'],
                 limitations=['미성공 계정의 성공까지 소요 시간은 아직 관측되지 않음'])
    value['tables']=[
        {'name':'accounts','grain':'계정별 1행','columns':[
            {'name':'account_id','description':'계정 식별자','generator':{'kind':'id'}},
            {'name':'level','description':'관측한 레벨','generator':{'kind':'integer','minimum':1,'maximum':100}}],
         'groups':[{'name':'all','count':20}]},
        {'name':'events','grain':'계정의 대상별 시도 1회','columns':[
            {'name':'event_id','description':'시도 식별자','generator':{'kind':'id'}},
            {'name':'account_id','description':'계정 식별자','generator':{'kind':'foreign_key','table':'accounts'}},
            {'name':'target','description':'분석 대상','generator':{'kind':'category','values':['a','b']}},
            {'name':'duration_sec','description':'시도 소요 시간','generator':{'kind':'integer','minimum':100,'maximum':600}},
            {'name':'wait_sec','description':'이전 종료 후 대기 시간','generator':{'kind':'integer','minimum':10,'maximum':200}},
            {'name':'success','description':'성공 여부','generator':{'kind':'integer','minimum':0,'maximum':1}},
            {'name':'started_at','description':'시도 시작','generator':{'kind':'timestamp_sequence','entity_column':'account_id','partition_columns':['target'],'interval_column':'wait_sec','duration_column':'duration_sec','start':'2026-09-01T00:00:00Z','end':'2026-09-30T00:00:00Z'}},
            {'name':'ended_at','description':'시도 종료','generator':{'kind':'timestamp_offset','source_column':'started_at','interval_column':'duration_sec'}}],
         'groups':[{'name':'all','count':120}]},
        {'name':'event_summary','grain':'계정과 대상별 관측 요약 1행','derived_from':'events','group_by':['account_id','target'],
         'unique_keys':[['account_id','target']], 'columns':[
             {'name':'summary_id','description':'요약 식별자','generator':{'kind':'id'}},
             {'name':'account_id','description':'계정 식별자','generator':{'kind':'group_key','source_column':'account_id','value_type':'integer'}},
             {'name':'target','description':'분석 대상','generator':{'kind':'group_key','source_column':'target','value_type':'category'}},
             {'name':'attempts','description':'실제 도전 횟수','generator':{'kind':'aggregate','operation':'count','value_type':'integer'}},
             {'name':'mean_duration','description':'실제 평균 소요 시간','generator':{'kind':'aggregate','operation':'avg','source_column':'duration_sec','value_type':'number'}},
             {'name':'first_start','description':'최초 시작','generator':{'kind':'aggregate','operation':'min','source_column':'started_at','value_type':'timestamp'}},
             {'name':'first_success_end','description':'최초 성공 종료, 미성공은 NULL','generator':{'kind':'aggregate','operation':'min','source_column':'ended_at','conditions':[{'column':'success','operator':'eq','value':1}],'value_type':'timestamp'}}]}]
    value['metrics']=[{'name':'event_rows','table':'events','operation':'count','minimum':120},
                      {'name':'success_rows','table':'events','operation':'count','conditions':[{'column':'success','operator':'eq','value':1}],'minimum':1},
                      {'name':'mean_duration','table':'event_summary','operation':'avg','column':'mean_duration'}]
    return value


@pytest.mark.parametrize('topic',['보스','구매','재화'])
def test_event_dependencies_and_source_summary_match(topic):
    recipe=Recipe.model_validate(event_recipe(topic)); rows=generate_rows(recipe,40)
    assert rows==generate_rows(recipe,40)
    for summary in rows['event_summary']:
        events=sorted([r for r in rows['events'] if (r['account_id'],r['target'])==(summary['account_id'],summary['target'])],key=lambda r:r['started_at'])
        assert summary['attempts']==len(events)
        assert summary['mean_duration']==sum(e['duration_sec'] for e in events)/len(events)
        assert summary['first_start']==events[0]['started_at']
        successes=[e['ended_at'] for e in events if e['success']==1]
        assert summary['first_success_end']==(min(successes) if successes else None)
        previous=timestamp('2026-09-01T00:00:00Z')
        for event in events:
            assert (timestamp(event['started_at'])-previous).total_seconds()==event['wait_sec']
            assert (timestamp(event['ended_at'])-timestamp(event['started_at'])).total_seconds()==event['duration_sec']
            previous=timestamp(event['ended_at'])


@pytest.mark.parametrize('change',['random_summary','unknown_group_key','bad_type','bad_offset','bad_partition','mixed_profile'])
def test_invalid_dependencies_rejected(change):
    value=event_recipe()
    if change=='random_summary':value['tables'][2]['columns'][4]['generator']={'kind':'integer','minimum':1,'maximum':100}
    if change=='unknown_group_key':value['tables'][2]['group_by']=['missing']
    if change=='bad_type':value['tables'][2]['columns'][4]['generator']['value_type']='category'
    if change=='bad_offset':value['tables'][1]['columns'][-1]['generator']['source_column']='duration_sec'
    if change=='bad_partition':value['tables'][1]['columns'][-2]['generator']['partition_columns']=['ended_at']
    if change=='mixed_profile':value['tables'][1]['columns'][1]['generator']['group']='missing'
    with pytest.raises(ValidationError):Recipe.model_validate(value)


def test_time_bucket_uses_kst_day_and_daily_summary_requires_date():
    value=event_recipe();events=value['tables'][1]
    events['columns'].append({'name':'day','description':'KST 관측 날짜','generator':{'kind':'timestamp_bucket','source_column':'started_at','bucket':'day','timezone':'Asia/Seoul'}})
    summary=value['tables'][2];summary['grain']='계정과 대상 및 일별 관측 요약'
    with pytest.raises(ValidationError):Recipe.model_validate(value)
    summary['group_by'].append('day');summary['columns'].append({'name':'day','description':'KST 날짜','generator':{'kind':'group_key','source_column':'day','value_type':'timestamp'}})
    summary['unique_keys']=[['account_id','target','day']]
    rows=generate_rows(Recipe.model_validate(value),40)
    import datetime as dt
    for event in rows['events']:
        bucket=timestamp(event['day']).astimezone(dt.timezone(dt.timedelta(hours=9)))
        assert bucket.hour==bucket.minute==bucket.second==0
        assert bucket.date()==timestamp(event['started_at']).astimezone(dt.timezone(dt.timedelta(hours=9))).date()


def test_public_promises_and_explicit_source_summary_are_enforced():
    from da_agent.adaptive_tasks import preflight
    from da_agent.task_contracts import RequestV2
    from da_agent.errors import DomainError
    value=bot_recipe();value['completion_conditions']=['숙련도 그룹별 비교']
    with pytest.raises(ValidationError):Recipe.model_validate(value)
    request=RequestV2(contract_version='request-v2',request_id='summary',message='원본 로그와 계산한 일별 요약을 제공해줘')
    with pytest.raises(DomainError):preflight(Recipe.model_validate(bot_recipe()),12,request)
    value=event_recipe();value['tables'][0]['columns'].append({'name':'total_attempts','description':'총 도전 횟수','generator':{'kind':'integer','minimum':1,'maximum':20}})
    with pytest.raises(ValidationError):Recipe.model_validate(value)


def test_db_infeasible_fixed_data_is_repaired_before_pinning(v2_db_client):
    client,provider=v2_db_client; calls=[]
    def review(messages):
        calls.append(messages)
        if 'schema' in json.loads(messages[1]['content']):
            value=bot_recipe()
            if len(calls)==1:value['metrics'][1]['minimum']=999
            return {'state':'completed','text':json.dumps(value)}
        return {'state':'completed','text':'{"aligned":true,"issues":[]}'}
    provider.review=review;rid=str(uuid.uuid4())
    client.post('/api/training/requests',json={'contract_version':'request-v2','request_id':rid,'message':'비정상 이용자 분석','data_mode':'adaptive'})
    result=client.get('/api/training/requests/'+rid).json()
    assert result['status']=='ready' and len(calls)==3
    assert 'data_dependency' in calls[1][-1]['content']


def test_db_dependencies_independent_source_sql_and_null(v2_db_client):
    client,provider=v2_db_client
    provider.review=lambda messages: {'state':'completed','text':json.dumps(event_recipe()) if 'schema' in json.loads(messages[1]['content']) else '{"aligned":true,"issues":[]}'}
    rid=str(uuid.uuid4())
    client.post('/api/training/requests',json={'contract_version':'request-v2','request_id':rid,'message':'구매까지 시간 분석','data_mode':'adaptive'})
    result=client.get('/api/training/requests/'+rid).json()
    assert result['status']=='ready',result
    aid=result['attempt_id']
    query='SELECT COUNT(*) FROM event_summary s WHERE attempts <> (SELECT COUNT(*) FROM events e WHERE e.account_id=s.account_id AND e.target=s.target) OR ABS(mean_duration-(SELECT AVG(duration_sec) FROM events e WHERE e.account_id=s.account_id AND e.target=s.target)) > 0.000001'
    actual=client.post('/api/attempts/'+aid+'/execute',json={'sql':query}).json()
    assert actual['status']=='success' and actual['rows']==[[0]]


@pytest.mark.parametrize('repaired',[True,False])
def test_db_alignment_repair_and_preserved_failure(v2_db_client,repaired):
    client,provider=v2_db_client; calls=[]
    def review(messages):
        calls.append(messages)
        if 'schema' in json.loads(messages[1]['content']):return {'state':'completed','text':json.dumps(bot_recipe())}
        if len(calls)==4 and repaired:return {'state':'completed','text':'{"aligned":true,"issues":[]}'}
        return {'state':'completed','text':'{"aligned":false,"issues":["원본과 요약의 관측값 불일치"]}'}
    provider.review=review;rid=str(uuid.uuid4())
    client.post('/api/training/requests',json={'contract_version':'request-v2','request_id':rid,'message':'비정상 이용자 분석','data_mode':'adaptive'})
    result=client.get('/api/training/requests/'+rid).json()
    assert len(calls)==4 and '원본과 요약의 관측값 불일치' in calls[2][-1]['content']
    with client.app.state.training.store.connect() as conn:
        fixed=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()['private']
    assert fixed['alignment_reviews'][0]['issues']==['원본과 요약의 관측값 불일치']
    assert 'alignment_reviews' not in result
    if repaired:assert result['status']=='ready'
    else:
        assert result['status']=='failed' and result['error_code']=='goal_mismatch' and not result['retry_allowed']
        before=copy.deepcopy(result)
        retry=client.post('/api/training/requests/'+rid+'/retry',json={'action_id':'retry','expected_revision':result['revision']})
        assert retry.status_code==409
        assert client.get('/api/training/requests/'+rid).json()==before
        assert before['failure_history'][0]['error_code']=='goal_mismatch'


def test_db_exhausted_legacy_retry_preserves_original_failure(v2_db_client):
    from psycopg.types.json import Jsonb
    from da_agent.adaptive_tasks import PLANNING_LIMIT
    client,provider=v2_db_client
    provider.review=lambda messages: {'state':'completed','text':json.dumps(bot_recipe()) if 'schema' in json.loads(messages[1]['content']) else '{"aligned":false,"issues":["필수 자료 부족"]}'}
    rid=str(uuid.uuid4())
    client.post('/api/training/requests',json={'contract_version':'request-v2','request_id':rid,'message':'비정상 이용자 분석','data_mode':'adaptive'})
    store=client.app.state.training.store
    with store.connect() as conn:
        payload=conn.execute('SELECT payload FROM training_requests WHERE request_id=%s',(rid,)).fetchone()['payload']
        payload.update(retry_allowed=True,planning_calls=PLANNING_LIMIT-1)
        conn.execute('UPDATE training_requests SET payload=%s WHERE request_id=%s',(Jsonb(payload),rid))
    before=client.get('/api/training/requests/'+rid).json()
    response=client.post('/api/training/requests/'+rid+'/retry',json={'action_id':'legacy-retry','expected_revision':before['revision']})
    assert response.status_code==409
    assert client.get('/api/training/requests/'+rid).json()==before
    assert before['error_code']=='goal_mismatch'


def test_db_invalid_alignment_correction_uses_remaining_schema_repair(v2_db_client):
    client,provider=v2_db_client;calls=[]
    def review(messages):
        calls.append(messages)
        if 'schema' in json.loads(messages[1]['content']):
            value=bot_recipe()
            if len(calls)==3:value['tables'][0]['name']='unsafe;table'
            return {'state':'completed','text':json.dumps(value)}
        return {'state':'completed','text':json.dumps({'aligned':len(calls)>2,'issues':[] if len(calls)>2 else ['필수 자료 부족']})}
    provider.review=review;rid=str(uuid.uuid4())
    client.post('/api/training/requests',json={'contract_version':'request-v2','request_id':rid,'message':'비정상 이용자 분석','data_mode':'adaptive'})
    result=client.get('/api/training/requests/'+rid).json()
    assert result['status']=='ready' and len(calls)==5
    with client.app.state.training.store.connect() as conn:
        fixed=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()['private']
    assert fixed['schema_reviews'] and fixed['alignment_reviews'][0]['passed'] is False
