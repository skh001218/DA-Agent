"""Text SQL contracts plus retention execution against disposable PostgreSQL."""
from copy import deepcopy
import json
import os
from types import SimpleNamespace as NS
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from da_agent.adaptive_tasks import Recipe, generate_rows, preflight
from da_agent.discord_generated_sql import check_quality, planning_messages, make_task, _answer
from da_agent.discord_generation import request
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore
from da_agent.sql_runner import SqlRunner
from da_agent.errors import DomainError
from test_discord_generation import MemoryStore


def retention_recipe(level):
    groups = [('a','organic','week1','yes',14),('b','ads','week1','no',14),
              ('c','organic','week2','yes',6),('d','ads','week2',None,14)]
    def category(value): return dict(kind='category',values=[value])
    def integer(value): return dict(kind='integer',minimum=value,maximum=value)
    grouping = [] if level=='beginner' else ['accounts.channel']
    if level=='advanced': grouping.insert(0,'accounts.cohort')
    metric = dict(name='d7_retention',table='retention_observations',operation='ratio',purpose='analysis',
        conditions=[dict(column='d7_returned',operator='eq',value='yes')],
        denominator_conditions=[dict(column='observed_days',operator='gte',value=7)],
        group_by=grouping,joins=[] if level=='beginner' else [dict(table='accounts',source_column='account_id')])
    question = '관측 완료 이용자의 D7 리텐션을 직접 SELECT로 계산하세요.'
    assumptions = ['2026-09-01~14 가입 대상의 이용자별 합성 D7 관측 요약이며 원본 로그가 아닙니다.',
        'D7은 가입 한국 날짜+7일 재접속 여부입니다. observed_days>=7인 이용자만 분모, d7_returned=yes가 분자입니다.',
        '무접속 이용자도 요약에 포함하고 7일 미관측 이용자는 분모에서 제외합니다.']
    return dict(status='ready',reason='요청한 D7 리텐션 SQL 풀이',topic='게임 이용자 리텐션',goal=question,
        title='관측 완료 이용자의 D7 리텐션',description='게임팀은 신규 이용자의 D7 리텐션을 확인하려 합니다. 연습용 합성 관측 요약으로 계산하세요.',
        difficulty=level,difficulty_reason='전체/채널/코호트·채널 집계와 관측 완료 조건',task_kind='calculation',
        completion_conditions=[question],limitations=['합성 요약은 실제 서비스 결과가 아닙니다.'],rubric='출력과 관측 완료 모집단을 검산합니다.',
        tables=[dict(name='accounts',grain='신규 이용자별 한 행',columns=[
            dict(name='account_id',description='이용자 기본키',generator=dict(kind='id')),
            dict(name='channel',description='가입 채널',generator=dict(kind='category',values=['organic','ads'])),
            dict(name='cohort',description='가입 코호트: week1=9월1~7일, week2=9월8~14일',generator=dict(kind='category',values=['week1','week2']))],
            groups=[dict(name=n,count=6,overrides=dict(channel=category(channel),cohort=category(cohort))) for n,channel,cohort,returned,days in groups]),
            dict(name='retention_observations',grain='이용자별 한 행의 합성 D7 관측 요약; 무접속 이용자 포함',unique_keys=[['account_id']],columns=[
                dict(name='observation_id',description='관측 요약 기본키',generator=dict(kind='id')),
                dict(name='account_id',description='accounts 기본키; 모든 가입자를 한 번씩 포함',generator=dict(kind='foreign_key',table='accounts')),
                dict(name='observed_days',description='가입 이후 관측을 완료한 일수; 7 미만은 D7 미관측',generator=dict(kind='integer',minimum=6,maximum=14)),
                dict(name='d7_returned',description='가입 한국 날짜+7일 재접속 yes/no; observed_days<7이면 미관측이므로 평가 제외',generator=dict(kind='category',values=['yes','no']))],
                groups=[dict(name=n,count=6,overrides=dict(account_id=dict(kind='foreign_key',table='accounts',group=n),
                    observed_days=integer(days),**(dict(d7_returned=category(returned)) if returned else {}))) for n,channel,cohort,returned,days in groups])],
        metrics=[dict(name='observation_rows',table='retention_observations',operation='count',minimum=24),metric],
        business_case=dict(provenance='synthetic',background='신규 이용자의 관측 완료 조건에 따른 D7 리텐션을 확인하는 가상 업무입니다.',
            observed_problem='채널과 가입 코호트에 따라 관측 완료 일수와 재접속 여부가 다른 합성 관측 자료를 제공합니다.',
            observation_period='가입: 한국 시간 2026-09-01~14, 관측 종료: 2026-09-29 00:00 미만',
            decision='SQL 실행 결과와 모집단을 확인하여 다음 연습에 활용합니다.',agent_assumptions=assumptions,
            requirements=[dict(competency=c,question=q,completion=q,metric_names=['d7_retention'] if c=='measurement' else [],
                evidence=[dict(table='retention_observations',columns=['account_id','observed_days','d7_returned'])]) for c,q in [
                    ('measurement',question),('decision','SQL 결과의 중복과 관측 조건 및 분모/NULL 검산 방법을 점검하세요.')]]))


class RetentionProvider:
    model='retention-test-fixture'
    generation_mode='synthetic'
    def __init__(self, level): self.level=level; self.calls=[]
    def review(self,messages):
        self.calls.append(deepcopy(messages))
        if '독립 검증자' in messages[0]['content']:
            value=dict(aligned=True,issues=[],quality_dimensions={k:'pass' for k in ('business_context','evidence_sufficiency','difficulty_fit','evaluation_alignment')})
        elif 'SQL 검산 방법' in messages[0]['content']:
            value=dict(checks=[dict(id=k,status='met',reason='공개 행 단위와 관측 조건 및 NULL 검산 절차가 구체적입니다.') for k in ('duplicates','period','denominator')])
        else: value=retention_recipe(self.level)
        return dict(state='completed',text=json.dumps(value,ensure_ascii=False),model=self.model)


@pytest.mark.parametrize('level',['beginner','intermediate','advanced'])
def test_retention_contract_and_independent_counts(level):
    recipe = Recipe.model_validate(retention_recipe(level))
    data = request('게임 D7 리텐션 SQL 문제','retention',level)
    preflight(recipe,731,data,quality_checker=check_quality)
    wanted, query = _answer(recipe,generate_rows(recipe,731))
    task = make_task({'intro_sections':{}},recipe,731)
    assert task['sql_contract']['columns'] == wanted['columns']
    assert 'SQL/코드/정답 수치' in planning_messages(data,[])[0]['content']
    assert sum(row[-3] for row in wanted['rows']) == 18
    assert all(row[-2] <= row[-3] for row in wanted['rows'])
    assert 'observed_days' in query and '>= 7' in query
    assert '튜토리얼' not in task['objective']
    assert query not in str(task)
    assert task['rubric']['criteria'] == task['sql_contract']['criteria']
    assert 'SQL 작성 역량' not in task['rubric']['non_scoring']


def test_arbitrary_topic_preserves_request_without_legacy_dispatch():
    service=DiscordTrainingService(MemoryStore(),NS(),None,NS())
    for topic in ('게임 D7 리텐션','아이템 매출','퀘스트별 완료율'):
        doc=service.start('u','g','c',uuid4().hex,text=topic,practice='sql')
        assert doc['practice']=='sql' and doc['generation']['original_message']==topic
    doc=service.start('u','g','c','same',text='게임 리텐션',practice='sql')
    with pytest.raises(DomainError,match='다른 출제'):
        service.start('u','g','c','same',text='게임 리텐션',practice='analysis')
    with pytest.raises(DomainError,match='기존 분석 연결'):
        service.start('u','g','c','linked',text='게임 리텐션',practice='sql',source_session_id=doc['session_id'])


def test_sql_per_user_ratio_rejects_missing_non_returning_accounts():
    raw=retention_recipe('beginner')
    raw['tables'][1]['groups'].pop(1)
    recipe=Recipe.model_validate(raw)
    with pytest.raises(ValueError,match='every target account'):
        check_quality(recipe)


def test_verification_review_receives_all_public_columns():
    from da_agent.discord_sql_practice import verification_review
    recipe=Recipe.model_validate(retention_recipe('advanced'))
    task=make_task({'intro_sections':{},'period':{},'schema':{
        t.name:{c.name:c.description for c in t.columns} for t in recipe.tables}},recipe,731)
    provider=RetentionProvider('advanced')
    result=verification_review(task,dict(execution_id='check',sql='SELECT 1',full_result={},
        verification_notes='retention_observations.observation_id별 COUNT(*)를 검산할 계획'),provider)
    envelope=json.loads(provider.calls[-1][1]['content'])
    assert 'observation_id' in envelope['public_schema']['retention_observations']
    assert envelope['public_objective']==task['objective']
    assert 'expected' not in envelope and 'private_reference' not in envelope
    assert result['status']=='충족'


def test_login_definition_is_not_a_promise_of_raw_logs():
    raw=retention_recipe('beginner')
    raw['description']='이용자별 합성 관측 요약을 제공합니다. D7은 가입일부터 7일 뒤 로그인 여부입니다.'
    recipe=Recipe.model_validate(raw)
    data=request('D7 로그인 여부의 이용자별 관측 요약을 제공해줘','login','beginner')
    preflight(recipe,731,data,quality_checker=check_quality)
    raw['description']='이용자별 합성 관측 요약을 제공합니다. 원본 접속 로그에서 계산한 자료가 아닙니다.'
    Recipe.model_validate(raw)
    raw['description']='원본 로그는 제공하지 않고 이용자별 합성 요약만 제공합니다.'
    recipe=Recipe.model_validate(raw)
    preflight(recipe,731,request('원본 로그 없이 이용자별 합성 요약을 제공해줘','summary','beginner'),quality_checker=check_quality)
    raw['description']='원본 로그와 이용자별 요약을 함께 제공합니다.'
    with pytest.raises(ValueError,match='source logs and summary'):
        Recipe.model_validate(raw)


def test_sql_clarification_keeps_practice_and_original_request():
    service=DiscordTrainingService(MemoryStore(),NS(),RetentionProvider('beginner'),NS())
    doc=service.start('u','g','c','sql-clarify',text='D7과 주간 재방문 중 어떤 지표로 풀지 정하고 싶어',practice='sql',difficulty='beginner')
    raw=retention_recipe('beginner'); raw.update(status='clarify',questions=['D7 리텐션과 주간 재방문 중 어느 지표를 원하시나요?'])
    service.provider.review=lambda messages:dict(state='completed',text=json.dumps(raw))
    ready=service.generate('u',doc['session_id'])
    assert ready['generation']['status']=='needs_clarification'
    assert ready['practice']=='sql' and ready['pending_question']['kind']=='generation_conditions'
    assert ready['generation']['original_message']==doc['generation']['original_message']


def test_semantic_retry_redesigns_instead_of_rechecking_same_recipe(monkeypatch):
    service=DiscordTrainingService(MemoryStore(),NS(),RetentionProvider('beginner'),NS())
    doc=service.start('u','g','c','semantic-retry',text='D7 리텐션 SQL',practice='sql',difficulty='beginner')
    with service.store.edit('u',doc['session_id']) as (saved,_):
        saved['state']='failed'; saved['generation'].update(status='failed',error_code='goal_mismatch')
    service.store.save_generation_job('u',doc['session_id'],dict(recipe=retention_recipe('beginner'),
        source_case=dict(version='discord-synthetic-v1',topic='D7'),alignment_repair_used=True,
        failures=[dict(code='goal_mismatch',issues=[dict(message='분모 0 그룹 출력 규칙 충돌')])]))
    calls=[]
    def stop_after_design(messages):
        calls.append(messages)
        service.handle('u',doc['session_id'],'cancel-retry','end')
        return dict(state='completed',text=json.dumps(retention_recipe('beginner')))
    service.provider.review=stop_after_design
    result=service.generate('u',doc['session_id'],retry=True)
    assert result['generation']['status']=='cancelled'
    assert len(calls)==1 and 'SQL 직접 풀이' in calls[0][0]['content']
    assert any('분모 0 그룹' in m['content'] for m in calls[0])


def sales_recipe(level):
    raw=retention_recipe(level)
    raw.update(topic='아이템 매출',title='채널별 이용자 구매액',goal='제공된 전체 이용자의 구매액 합계를 계산',
        description='가상 게임에서 이용자별 구매액 합성 요약으로 채널과 코호트의 매출 합계를 계산합니다.')
    table=raw['tables'][1]; table['name']='purchase_summary'; table['grain']='이용자별 한 행의 합성 구매액 요약'
    table['columns']=[c for c in table['columns'] if c['name'] not in ('observed_days','d7_returned')]
    table['columns'].append(dict(name='amount',description='해당 이용자의 기간 내 구매액',generator=dict(kind='number',minimum=0,maximum=200)))
    for group in table['groups']:
        group['overrides']={k:v for k,v in group['overrides'].items() if k=='account_id'}
    raw['metrics'][0].update(table='purchase_summary',name='purchase_rows')
    raw['metrics'][1].update(name='revenue',table='purchase_summary',operation='sum',column='amount',conditions=[],denominator_conditions=[])
    case=raw['business_case']; case['agent_assumptions']=['합성 자료의 전체 이용자 구매액을 집계합니다. 구매하지 않은 이용자도 금액 0으로 포함합니다.']
    case['requirements'][0].update(question='전체 구매액 합계를 SELECT로 계산하세요.',completion='주어진 그룹과 전체 이용자 구매액 합계를 출력합니다.',metric_names=['revenue'])
    for req in case['requirements']:
        req['evidence']=[dict(table='purchase_summary',columns=['account_id','amount'])]
    return raw


@pytest.fixture
def sql_service(tmp_path):
    admin,learner,records=[os.getenv(k) for k in ('DISCORD_TEST_ADMIN_DSN','DISCORD_TEST_LEARNER_DSN','DISCORD_TEST_RECORDS_DSN')]
    if not all((admin,learner,records)): pytest.skip('Requires disposable PostgreSQL DSNs')
    settings=NS(admin_dsn=admin,learner_dsn=learner,records_dsn=records,generation_directory=str(tmp_path),
        query_timeout_ms=5000,max_rows=1000,max_bytes=1048576,preview_rows=10,execution_ttl=600,daily_call_limit=30)
    store=DiscordStore(records); store.initialize()
    service=DiscordTrainingService(store,NS(runner=SqlRunner(settings)),RetentionProvider('beginner'),settings)
    owner=uuid4().hex
    yield service,owner
    for doc in store.list(owner,'sql-test'):
        schemas={c['schema_name'] for c in doc.get('sql_checks',[])}
        if doc.get('schema_name'): schemas.add(doc['schema_name'])
        with psycopg.connect(admin) as conn:
            for name in schemas: conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))


@pytest.mark.parametrize('level',['beginner','intermediate','advanced'])
def test_db_text_retention_sql_flow(sql_service,level):
    service,owner=sql_service
    service.provider=RetentionProvider(level)
    doc=service.start(owner,'sql-test','parent',uuid4().hex,text='게임 D7 리텐션을 계산하는 SQL 문제',practice='sql',difficulty=level)
    doc=service.generate(owner,doc['session_id'])
    assert doc['generation']['status']=='ready',doc['generation']
    sid=doc['session_id']
    private=service.store.generation_job(owner,sid)['sql_reference']
    assert len(private['checks'])==4 and 'sql_reference' not in doc
    assert any(c['expected'] != private['checks'][0]['expected'] for c in private['checks'][1:3])
    assert private['sql'] not in json.dumps(doc,ensure_ascii=False)
    assert all('expected' not in c for c in doc['sql_checks'])
    def handle(action,text='',payload=None): return service.handle(owner,sid,uuid4().hex,action,text,payload)
    assert '먼저' in handle('help',payload={'help_type':'solution'})['messages'][0]
    # Error and revision through the exact learner submission path.
    error=handle('sqlrun','```sql\nSELECT absent FROM retention_observations\n```')
    assert error['session']['sql_attempts'][-1]['result']['status']=='error'
    equivalent='WITH answer AS ('+private['sql']+') SELECT * FROM answer'
    notes='\n검산 계획: account_id별 COUNT가 1인지 확인해 조인 중복을 점검합니다. observed_days=6과 7의 포함 차이를 확인합니다. 무접속 no가 분모에 남는지, 빈 자료의 그룹 집계는 0행인지, 별도 전체 집계는 분모 0 및 비율 NULL인지 확인합니다.'
    answer=handle('sqlrun','```sql\n'+equivalent+'\n```'+notes)
    assert answer['session']['sql_attempts'][-1]['result']['status']=='success'
    submitted=handle('submit')
    evaluation=submitted['session']['evaluations'][-1]['result']
    assert not evaluation['held'] and all(c['status']=='충족' for c in evaluation['criteria'])
    assert all(c['matched'] for c in evaluation['checks'])
    assert submitted['submission']['completed']
    assert all(c['schema_name'] not in json.dumps(submitted['submission']) for c in private['checks'])
    restored=DiscordTrainingService(DiscordStore(service.settings.records_dsn),service.engine,service.provider,service.settings).resume(owner,'sql-test',sid)
    assert restored['session']['sql_attempts'][-1]['sql']==equivalent
    # Dropping incomplete-observation filtering changes the denominator.
    wrong=private['sql'].replace('"retention_observations"."observed_days" >= 7','"retention_observations"."observed_days" >= 0')
    assert wrong!=private['sql']
    handle('sqlrun','```sql\n'+wrong+'\n```'+notes)
    bad=handle('submit')['session']['evaluations'][-1]['result']
    assert next(c for c in bad['criteria'] if c['id']=='calculation')['status']=='보완 필요'
    blocked=handle('sqlrun','```sql\nSELECT * FROM tutorial_attempts\n```')
    assert blocked['session']['sql_attempts'][-1]['result']['status']=='blocked'
    if level=='beginner':
        handle('sqlrun','```sql\nSELECT 18 AS denominator, 6 AS numerator, 0.333333::numeric AS d7_retention\n```')
        hardcoded=handle('submit')['session']['evaluations'][-1]['result']
        assert next(c for c in hardcoded['criteria'] if c['id']=='robustness')['status']=='보완 필요'
    else:
        # A VALUES answer guarded by a real table also returns zero rows on
        # the empty case. Changed sample sizes must still reject its constants.
        values=', '.join('('+', '.join(repr(v) for v in row)+')' for row in private['checks'][0]['expected'])
        columns=', '.join(doc['task']['sql_contract']['columns'])
        constant=f'SELECT * FROM (VALUES {values}) AS answer({columns}) WHERE EXISTS (SELECT 1 FROM retention_observations)'
        constant_result=handle('sqlrun','```sql\n'+constant+'\n```'+notes)
        assert constant_result['session']['sql_attempts'][-1]['result']['status']=='success'
        literal=handle('submit')['session']['evaluations'][-1]['result']
        assert next(c for c in literal['criteria'] if c['id']=='robustness')['status']=='보완 필요'
    # No automatic SQL/exposure; only explicit post-attempt solution.
    assert handle('help',payload={'help_type':'solution'})['messages'][0]==private['sql']


def test_db_generated_sql_cancellation_revokes_private_checks(sql_service,monkeypatch):
    service,owner=sql_service
    from da_agent.discord_generated_sql import prepare_checks
    doc=service.start(owner,'sql-test','parent',uuid4().hex,text='리텐션 SQL',practice='sql',difficulty='beginner')
    captured=[]
    def cancel(*args,**kwargs):
        value=prepare_checks(*args,**kwargs)
        captured.extend(c['schema_name'] for c in value['checks'])
        service.handle(owner,doc['session_id'],uuid4().hex,'end')
        return value
    monkeypatch.setattr('da_agent.discord_generated_sql.prepare_checks',cancel)
    result=service.generate(owner,doc['session_id'])
    assert result['generation']['status']=='cancelled'
    with psycopg.connect(service.settings.admin_dsn) as conn:
        from urllib.parse import urlparse
        role=urlparse(service.settings.learner_dsn).username
        for schema in captured:
            assert not conn.execute('SELECT has_schema_privilege(%s,%s,\'USAGE\')',(role,schema)).fetchone()[0]
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_db_non_retention_topic_and_execution_hold(sql_service):
    service,owner=sql_service
    original=service.provider.review
    def review(messages):
        if '독립 검증자' in messages[0]['content']: return original(messages)
        return dict(state='completed',text=json.dumps(sales_recipe('intermediate')),model=service.provider.model)
    service.provider.review=review
    doc=service.start(owner,'sql-test','parent',uuid4().hex,text='이용자 구매액을 채널별로 합계 내는 SQL 문제',practice='sql',difficulty='intermediate')
    doc=service.generate(owner,doc['session_id'])
    assert doc['generation']['status']=='ready',doc['generation']
    sid=doc['session_id']; private=service.store.generation_job(owner,sid)['sql_reference']
    assert doc['task']['topic']=='아이템 매출' and 'd7_retention' not in str(doc['task']['sql_contract'])
    answer=service.handle(owner,sid,uuid4().hex,'sqlrun','```sql\n'+private['sql']+'\n```')
    assert answer['session']['sql_attempts'][-1]['result']['status']=='success'
    submitted=service.handle(owner,sid,uuid4().hex,'submit')
    assert all(c['status']=='충족' for c in submitted['session']['evaluations'][-1]['result']['criteria'])
    service.settings.learner_dsn='postgresql://sql057_reader:unused@127.0.0.1:1/discord_test_sql057'
    held=service.handle(owner,sid,uuid4().hex,'sqlrun','```sql\n'+private['sql']+'\n```')
    assert held['session']['sql_attempts'][-1]['result']['status']=='error'
    # Choose saved success to force alternate-case execution after connection failure.
    # Cached evaluation needs no rerun; change exposure to request a new evaluation.
    with service.store.edit(owner,sid) as (saved,_): saved['sql_exposure']='열람 확인'
    result=service.handle(owner,sid,uuid4().hex,'submit',payload={'execution_id':answer['session']['sql_attempts'][-1]['execution_id']})
    assert result['session']['evaluations'][-1]['result']['held']


def test_db_missing_private_reference_preserves_reading_and_blocks_grading(sql_service):
    service,owner=sql_service
    doc=service.start(owner,'sql-test','parent',uuid4().hex,text='D7 리텐션 SQL',practice='sql',difficulty='beginner')
    doc=service.generate(owner,doc['session_id'])
    assert doc['generation']['status']=='ready'
    sid=doc['session_id']; job=service.store.generation_job(owner,sid)
    answer=service.handle(owner,sid,uuid4().hex,'sqlrun','```sql\n'+job['sql_reference']['sql']+'\n```')
    eid=answer['session']['sql_attempts'][-1]['execution_id']
    job.pop('sql_reference'); service.store.save_generation_job(owner,sid,job)
    assert '데이터 사전' in service.handle(owner,sid,uuid4().hex,'help',payload={'help_type':'data_dictionary'})['messages'][0]
    assert service.handle(owner,sid,uuid4().hex,'sql',payload={'execution_id':eid})['messages'][0]
    assert '출제 기준' in service.handle(owner,sid,uuid4().hex,'submit')['messages'][0]
