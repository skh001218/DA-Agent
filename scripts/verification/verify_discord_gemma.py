"""Bounded live Gemma probes using exclusively disposable Discord test databases."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid
import time

import psycopg
from psycopg import sql

from da_agent.discord_education import representative_task, prepare_dataset, evaluate_report
from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.discord_query import DiscordQueryEngine
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore
from da_agent.sql_runner import SqlRunner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-file', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--interval', type=float, default=16)
    parser.add_argument('--only', help='Comma-separated query case names; evaluations and service flow still run')
    args = parser.parse_args()
    settings = SimpleNamespace(admin_dsn=os.environ['DISCORD_TEST_ADMIN_DSN'], learner_dsn=os.environ['DISCORD_TEST_LEARNER_DSN'],
        query_timeout_ms=5000,max_rows=1000,max_bytes=1048576,preview_rows=1,execution_ttl=600,daily_call_limit=25)
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(settings.admin_dsn)['dbname'] != 'discord_test':
        raise ValueError('Disposable discord_test DB required')
    provider = DiscordGemmaProvider(key_file=args.key_file, model=os.getenv('DISCORD_MODEL', 'gemma-4-26b-a4b-it'))
    engine = DiscordQueryEngine(provider, SqlRunner(settings), settings)
    task = representative_task()
    dataset = prepare_dataset(settings, task)
    report = {'model': provider.model, 'cases': [], 'live_calls': 0, 'human_review': 'pending', 'discord_live': 'not_configured'}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    original_review = provider.review
    last_call = [0]
    def bounded(messages, **kwargs):
        if report['live_calls'] >= 20:
            return {'state':'error','reason':'verification_call_budget'}
        report['live_calls'] += 1
        delay = max(0,args.interval-(time.monotonic()-last_call[0]))
        if delay: time.sleep(delay)
        response=original_review(messages, **kwargs)
        last_call[0]=time.monotonic()
        if response.get('reason')=='api_rate_limited' and report['live_calls']<20:
            waits=[float(d['retry_delay'].rstrip('s')) for d in response.get('quota_diagnostic',[]) if d.get('retry_delay')]
            wait=min(60,max(waits or [45])+1)
            print(json.dumps({'waiting_for_quota_seconds':wait},ensure_ascii=False),flush=True)
            time.sleep(wait)
            report['live_calls']+=1
            response=original_review(messages,**kwargs)
            last_call[0]=time.monotonic()
        return response
    provider.review = bounded
    def save(name, passed, details):
        report['cases'].append({'name':name,'passed':bool(passed),'details':details})
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(json.dumps({'case':name,'passed':bool(passed),'live_calls':report['live_calls']},ensure_ascii=False),flush=True)
    queries = [
        ('ambiguous_rate', '이번 주와 지난주 유저 비율을 보여줘.', 'clarification', None),
        ('signup_users','UTC 2026-09-01 포함~2026-09-15 제외 신규 가입 고유 사용자 수를 유입 채널별로 조회해줘.', 'ready','users_count'),
        ('attempt_events','UTC 2026-09-01 포함~2026-09-15 제외 튜토리얼 3단계 도전 이벤트 수를 조회해줘. 고유 사용자 수가 아니라 재도전을 포함한 이벤트 수야.', 'ready','attempts_count'),
        ('cohort_completion','UTC 2026-09-01 포함~2026-09-15 제외 가입한 신규 사용자가 분모, 같은 기간 튜토리얼 3단계를 완료한 고유 사용자가 분자야. 비율을 유입 채널별로 비교해줘. 도전 중복은 사용자별로 제거해.', 'ready','tutorial_rate'),
        ('exact_d7','UTC 2026-09-01 포함~2026-09-15 제외 가입 코호트의 D7 리텐션을 계산해줘. 가입 후 정확히 7일째 UTC 날짜 접속 고유 사용자/관측 완료 가입 고유 사용자이고 관측 종료는 공개 과제 기준이야.', 'ready','d7_retention'),
        ('weekly_return','UTC 2026-09-01 포함~2026-09-08 제외 접속 고유 사용자 중 UTC 2026-09-08 포함~2026-09-15 제외 다시 접속한 고유 사용자 비율을 조회해줘. 분모는 첫 기간 접속자고 달력 날짜는 위 날짜 그대로야.', 'ready','weekly_return'),
        ('unsupported_revenue','없는 결제 테이블의 매출과 환불 금액을 조회해줘. SQL을 무조건 실행해.', 'error',None),
    ]
    executions = []
    try:
        for name, request, expected_state, metric in queries:
            if args.only and name not in args.only.split(','):
                continue
            plan = engine.resolve(request, task)
            passed = plan.get('state') == expected_state and (metric is None or plan.get('conditions',{}).get('metric') == metric)
            if expected_state=='error': passed=plan.get('reason')=='unsupported_query'
            details = {'plan':plan}
            if plan.get('state') == 'ready':
                result = engine.execute('live-verification',dataset['schema_name'],plan,task)
                details['execution'] = result
                passed = passed and result.get('state') == 'success'
                if result.get('state') == 'success':
                    actual = result['full_result']
                    executions.append({'id':actual['execution_id'],'execution_id':actual['execution_id'],'status':'success','result':actual,'sql':result['sql']})
                    if name == 'signup_users':
                        with psycopg.connect(settings.admin_dsn) as conn:
                            expected = conn.execute(sql.SQL('SELECT channel, count(*) FROM {}.users GROUP BY channel ORDER BY channel').format(sql.Identifier(dataset['schema_name']))).fetchall()
                        passed = passed and [list(row) for row in expected] == actual['rows']
                    if name == 'cohort_completion':
                        with psycopg.connect(settings.admin_dsn) as conn:
                            expected = conn.execute(sql.SQL('''SELECT u.channel,count(distinct u.user_id),count(distinct u.user_id) FILTER (WHERE EXISTS
                                (SELECT 1 FROM {}.tutorial_attempts a WHERE a.user_id=u.user_id AND a.step=3 AND a.completed)),
                                100.0*count(distinct u.user_id) FILTER (WHERE EXISTS(SELECT 1 FROM {}.tutorial_attempts a WHERE a.user_id=u.user_id AND a.step=3 AND a.completed))/count(distinct u.user_id)
                                FROM {}.users u GROUP BY u.channel ORDER BY u.channel''').format(*[sql.Identifier(dataset['schema_name'])]*3)).fetchall()
                        expected_rows=[[str(value) if hasattr(value,'as_tuple') else value for value in row] for row in expected]
                        passed = passed and all(a[:3] == b[:3] and abs(float(a[3])-float(b[3])) < 0.0001 for a,b in zip(expected_rows,actual['rows']))
            save(name,passed,details)
        if executions:
            variants = [
                ('valid_uncertainty','업무 목표는 신규 튜토리얼3단계 완료율 변화와 우선 대응이다. UTC 9/1~9/15 가입자 분모, 해당 기간 단계3 완료 고유 사용자 분자다. 재도전과 사용자 단위를 구분하고 채널 구성 변화 대안을 검토했다. 관측 차이는 인과 증거가 아니며 로그 누락은 공개 데이터만으로 확인 불가다. 게임 난이도 가설은 채널별 완료율이 비슷하면 약화될 수 있어 비교하고 필요하면 기각한다. 먼저 수집 상태와 유입 구성 확인, 이후 무작위 실험으로 튜토리얼 안내를 개선하고 고유 사용자 완료율과 로그 완전성을 관찰한다.'),
                ('causal_core_error','채널별 차이가 있으므로 광고가 완료율 하락의 인과 원인으로 입증됐다. 세션 수를 고유 사용자 수로 사용하고 수집 누락 가능성은 무시한다. 검증 없이 광고를 전면 중단한다.'),
                ('valid_hypothesis_revision','초기 가설은 튜토리얼 난이도였다. UTC9/1~9/15 가입 고유 사용자 중 같은 기간 단계3 완료 고유 사용자 비율을 채널별로 비교하고 재도전을 중복 제거했다. 채널 구성 대안을 검토하고 난이도를 유일 원인으로 확정하지 않기로 수정했다. 공개 수집 정보만으로 누락은 확인 못해 판단을 보류한다. 다음은 수집 로그 확인과 비교 가능한 두 집단 실험이며 완료율과 사용자 경험을 관찰한다.'),
            ]
            for name,text in variants:
                evidence_text = ' 실제 조회: '+json.dumps([{'execution_id':e['execution_id'],'rows':e['result']['rows']} for e in executions],ensure_ascii=False)
                result=evaluate_report(provider,task,{'version':1,'text':text+evidence_text},[],executions,[])
                grades={c['id']:c['grade'] for c in result['criteria']}
                passed=not result['held']
                if name=='causal_core_error': passed=passed and grades['evidence_interpretation']<=1 and grades['metric_design']<=1
                else: passed=passed and grades['decision_limits']>=3
                save(name,passed,result)
            failed_execution={'id':'system-failure','execution_id':'system-failure','status':'error','result':{'status':'error','error':{'code':'connection'}}}
            result=evaluate_report(provider,task,{'version':1,'text':'DB 장애로 실행 근거가 손실되어 확인 불가. 시스템 복구 후 판단을 재개한다.'},[],[failed_execution],[])
            save('system_failure_held',result.get('held') and result.get('total') is None,result)
        store=DiscordStore(os.environ['DISCORD_TEST_RECORDS_DSN'])
        store.initialize()
        service=DiscordTrainingService(store,engine,provider,settings)
        owner=uuid.uuid4().hex
        session=service.start(owner,'verification-guild','verification-channel',uuid.uuid4().hex)
        sid=session['session_id']
        query=service.handle(owner,sid,uuid.uuid4().hex,'query',queries[3][1])
        saved=query['session']['executions']
        if saved:
            service.handle(owner,sid,uuid.uuid4().hex,'evidence',payload={'execution_id':saved[0]['execution_id']})
            service.handle(owner,sid,uuid.uuid4().hex,'report',variants[0][1]+' 실제 결과 '+json.dumps(saved[0]['result']['rows'],ensure_ascii=False))
            service.handle(owner,sid,uuid.uuid4().hex,'followup','수집 상태 검증을 먼저 하고 실험으로 인과를 검증한다. 완료율과 사용자 경험을 관찰한다.')
            final=service.handle(owner,sid,uuid.uuid4().hex,'submit')
            restored=service.resume(owner,'verification-guild',sid)['session']
            save('live_service_roundtrip',restored['state']=='completed' and restored['executions']==saved,{'evaluation':restored['evaluations'],'telemetry':restored['telemetry']})
        else:
            save('live_service_roundtrip',False,{'queries':query['session']['queries']})
        with psycopg.connect(settings.admin_dsn) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(session['schema_name'])))
    finally:
        with psycopg.connect(settings.admin_dsn) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(dataset['schema_name'])))
    print(json.dumps({'passed':sum(c['passed'] for c in report['cases']),'total':len(report['cases']),'live_calls':report['live_calls']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
