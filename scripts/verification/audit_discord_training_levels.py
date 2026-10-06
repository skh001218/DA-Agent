"""Live educational audit. Uses a dedicated audit database and never sends Discord messages."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

import psycopg
from da_agent.discord_education import representative_task, fixture_dataset, evaluate_report
from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.discord_query import DiscordQueryEngine, compile_query
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore
from da_agent.discord_presentation import task_intro
from da_agent.discord_tables import render_table_png, result_table
from da_agent.sql_runner import SqlRunner


def main():
    out = Path('/tmp/training-level-audit')
    out.mkdir(exist_ok=True)
    def dsn(key):
        parts = urlsplit(os.environ[key])
        return urlunsplit(parts._replace(path='/discord_audit_20261006'))
    settings = SimpleNamespace(admin_dsn=dsn('DISCORD_ADMIN_DSN'), learner_dsn=dsn('DISCORD_LEARNER_DSN'),
        query_timeout_ms=5000, max_rows=1000, max_bytes=1048576, preview_rows=10, execution_ttl=600, daily_call_limit=30)
    provider = DiscordGemmaProvider(key_file='/run/secrets/gemma_key', model=os.environ['DISCORD_MODEL'])
    original = provider.review
    audit = dict(date='2026-10-06', scope='Discord service, actual Gemma and PostgreSQL; no Discord delivery',
                 calls=[], levels={}, probes=[], human_review='pending')
    last = [0.0]
    def save():
        (out / 'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    def review(messages, **kwargs):
        if len(audit['calls']) >= 24:
            return {'state':'error','reason':'audit_call_budget'}
        delay = max(0, 16 - (time.monotonic() - last[0]))
        if delay: time.sleep(delay)
        response = original(messages, **kwargs)
        last[0] = time.monotonic()
        audit['calls'].append({k:response[k] for k in ('state','reason','model','usage','text') if k in response})
        save()
        print(json.dumps({'call':len(audit['calls']), 'state':response.get('state'), 'reason':response.get('reason')}, ensure_ascii=False), flush=True)
        return response
    provider.review = review
    runner = SqlRunner(settings)
    engine = DiscordQueryEngine(provider, runner, settings)
    store = DiscordStore(settings.admin_dsn)
    store.initialize()
    service = DiscordTrainingService(store, engine, provider, settings)
    def event(): return uuid4().hex
    for level in ('beginner','intermediate','advanced'):
        owner = 'audit-' + level + '-' + event()
        doc = service.start(owner, 'audit-local', 'audit-local', event(), difficulty=level)
        sid = doc['session_id']
        record = dict(task=doc['task'], session_id=sid, transcript=[], canonical=[], report=None)
        audit['levels'][level] = record
        (out / (level + '-task.txt')).write_text(task_intro(doc), encoding='utf-8')
        def handle(action, text='', payload=None):
            response = service.handle(owner, sid, event(), action, text, payload or {})
            record['transcript'].append(dict(action=action, text=text, response=response.get('messages'), query=response['session']['queries'][-1] if action in ('query','answer') and response['session']['queries'] else None))
            save()
            return response['session']
        handle('hypothesis', '가설: 2주차 완료율 변화에는 튜토리얼 난이도 변화 또는 유입 채널 구성 변화가 연결될 수 있다. 채널 내 비율이 유지되면 난이도 악화 가설은 약해진다.')
        for start,end in [('2026-09-01','2026-09-08'),('2026-09-08','2026-09-15')]:
            request = f'UTC {start} 포함부터 {end} 제외까지 가입한 신규 고유 사용자 전체를 분모로, 이들 중 UTC 2026-09-01 포함부터 2026-10-01 제외까지 튜토리얼 3단계를 완료한 고유 사용자를 분자로 완료율을 유입 채널별로 조회해줘. 재도전은 사용자별로 제거해줘.'
            doc = handle('query', request)
            if doc.get('pending_query'):
                doc = handle('answer', request + ' 집계 단위는 사용자이고 그룹은 유입 채널, 다른 필터는 없어.')
        if level != 'beginner':
            doc = handle('query', 'UTC 2026-09-01 포함부터 2026-09-15 제외까지 튜토리얼 3단계 도전의 중복 이벤트 수를 유입 채널별로 조회해줘. 같은 사용자와 단계의 추가 재도전 이벤트 수이며 수집 중복으로 단정하지 않을게.')
        if level == 'advanced':
            doc = handle('query', 'UTC 2026-09-01 포함부터 2026-09-15 제외까지 가입한 신규 고유 사용자 중 D7 관측 완료 사용자를 분모로, 가입일+7일 UTC 날짜에 접속한 고유 사용자를 분자로 D7 리텐션을 유입 채널별로 조회해줘. 관측 종료는 공개 과제 기준이고 중복 접속은 사용자별로 제거해줘.')
        # Independently compiled reference calculations are never substituted for a failed NL query.
        for start,end in [('2026-09-01','2026-09-08'),('2026-09-08','2026-09-15')]:
            c=dict(metric='tutorial_rate',start=start,end=end,timezone='UTC',period_basis='explicit_dates',unit='user',group_by='channel',filters={},step=3,event_start='2026-09-01',event_end='2026-10-01',numerator='completed_users',denominator='signup_users')
            query=compile_query(c, doc['task'])
            result=runner.execute('audit-reference',doc['schema_name'],query)
            record['canonical'].append(dict(conditions=c,sql=query,result=result))
        actual = store.get(owner,sid)
        record['executions'] = actual['executions']
        for execution in actual['executions']:
            handle('evidence', payload={'execution_id':execution['execution_id']})
        if actual['executions']:
            for index,blob in enumerate(render_table_png(result_table(actual['executions'][0]))):
                (out / f'{level}-result-{index+1}.png').write_bytes(blob)
        usable = [e for e in actual['executions'] if e.get('conditions',{}).get('metric')=='tutorial_rate']
        # Require the actual saved NL plans, not the diagnostic reference, for learner reporting.
        usable = [q for q in actual['queries'] if q['plan'].get('state')=='ready' and q['plan'].get('conditions',{}).get('metric')=='tutorial_rate']
        if len(usable) < 2:
            record['blocked']='Two cohort completion queries were not both executed. No simulated successful submission.'
            save(); continue
        facts = [{'execution_id':e['execution_id'],'sql':e['sql'],'columns':e['result']['columns'],'rows':e['result']['rows']} for e in actual['executions']]
        cohort_executions = actual['executions'][:2]
        weekly = []
        for execution in cohort_executions:
            rows = execution['result']['rows']
            weekly.append(dict(n=sum(int(r[1]) for r in rows), complete=sum(int(r[2]) for r in rows), channels={r[0]:dict(n=int(r[1]),complete=int(r[2]),rate=float(r[3])) for r in rows}))
        record['observed_weekly'] = weekly
        observations = f"1주차 {weekly[0]['complete']}/{weekly[0]['n']}={100*weekly[0]['complete']/weekly[0]['n']:.2f}%, 2주차 {weekly[1]['complete']}/{weekly[1]['n']}={100*weekly[1]['complete']/weekly[1]['n']:.2f}%. 채널별 실제 값: "+json.dumps(weekly,ensure_ascii=False)
        report = ('업무 질문: 9월1~7일과 9월8~14일 신규 가입자 코호트의 튜토리얼3단계 완료율 변화와 우선 대응. UTC 시작 포함/끝 제외, 완료 관측은 10월1일 제외까지. '
            '신규 가입 고유 사용자 전체가 분모, 해당 코호트에서 완료한 고유 사용자가 분자이며 재도전 중복은 EXISTS로 제거한다. '
            '초기 난이도 악화 가설과 유입 구성 변화 가설을 구분하려고 주별 채널 비교를 선택했다. '
            '전체 완료율은 주별 분자 합/분모 합으로 구한다. 채널 내 변화와 채널 가입자 비중을 함께 보고 단순 전체 차이로 원인을 확정하지 않는다. '
            '실제 수치는 '+observations+' '
            'organic은 80%로 유지되고 ads는 80%에서66.67%로 하락했다. ads 비중은25%에서75%로 증가했다. '
            '1주차 채널 비율이 모두80%이므로 1주차 비율을 고정하면 유입 구성만 바꿔도 전체80%다. 따라서 구성 변화만으로 전체 하락을 설명하는 가설은 기각한다. 채널 내 관측 변화도 확인해야 한다. 모집 경로의 인과 효과는 확인 불가다. '
            '40명 합성 데이터이고 작은 채널 표본이며 재도전은 수집 중복과 다르다. 수집 누락, 버전, 기기, 숙련도는 관측할 수 없다. '
            '튜토리얼 하향이나 광고 중단을 즉시 결정하지 않고 이벤트 수집 및 채널 구성 점검을 우선한다. '
            '후속 검증은 기기/버전/숙련도 로그를 추가하고 신규 이용자를 대상으로 튜토리얼 변경 무작위 실험을 설계한다. '
            '완료율을 주지표, D7과 오류율을 보호지표로 보고 실험 전 최소 탐지 효과와 표본 수를 정한다. 현재 자료로 검정력이나 인과는 확정하지 않는다. '
            '실제 실행 근거: '+json.dumps(facts,ensure_ascii=False))
        if level == 'beginner':
            report = ('두 주 신규 가입자 전체 중 튜토리얼3단계 완료한 고유 사용자 비율을 비교했다. UTC 가입 기간은 9월1~7일 및8~14일, 완료 관측은10월1일 제외까지다. 중복 시도를 사용자 단위로 제거했다. '+observations+
                ' 전체 완료율은10%p 낮아졌다. 이것만으로 튜토리얼이 어려워졌다고 확정할 수 없다. 40명 합성 표본이며 실제 로그 누락은 확인 불가다. 우선 수집 상태와 추가 표본을 확인한다. 근거: '+json.dumps(facts,ensure_ascii=False))
        elif level == 'intermediate':
            report = report.replace('후속 검증은 기기/버전/숙련도 로그를 추가하고 신규 이용자를 대상으로 튜토리얼 변경 무작위 실험을 설계한다. ','후속 검증은 기기/버전/숙련도 로그와 추가 표본 비교다. ').replace('완료율을 주지표, D7과 오류율을 보호지표로 보고 실험 전 최소 탐지 효과와 표본 수를 정한다. 현재 자료로 검정력이나 인과는 확정하지 않는다. ','현재 자료로 통계적 유의성이나 인과는 확정하지 않는다. ')
        record['report']=report
        handle('report', report)
        handle('followup','수집 및 유입 채널 구성을 먼저 확인한다. 현재 관측만으로 광고나 튜토리얼의 인과 원인을 단정하지 않는다. 변경 시 무작위 배정과 관측 기간을 사전 고정하고 완료율·D7·오류율 및 신뢰구간을 확인한다.')
        doc=handle('submit')
        record['final']=doc
        restored=service.resume(owner,'audit-local',sid)['session']
        record['restored_identical']=restored['reports']==doc['reports'] and restored['executions']==doc['executions'] and restored['evaluations']==doc['evaluations']
        save()
        print(json.dumps({'level':level,'state':doc['state'],'total':doc['evaluations'][-1]['result']['total'],'held':doc['evaluations'][-1]['result']['held']},ensure_ascii=False),flush=True)
    tasks=[representative_task(difficulty=l) for l in ('beginner','intermediate','advanced')]
    normalized=[{k:v for k,v in t.items() if k not in ('difficulty','help_policy')} for t in tasks]
    audit['identical_task_except_help_and_label']=normalized[0]==normalized[1]==normalized[2]
    audit['identical_data']=fixture_dataset(tasks[0])==fixture_dataset(tasks[1])==fixture_dataset(tasks[2])
    advanced=audit['levels']['advanced']
    if advanced.get('final'):
        doc=advanced['final']
        for name, text in [('causal_error','채널별 완료율 차이가 있으므로 광고 유입이 완료율 하락의 확정 원인이다. 광고를 즉시 중단하면 완료율이 반드시 회복한다.'),('unsupported_number','2주차 전체 완료율은 10%이다. 실행 결과가 달라도 이 숫자가 정답이다. 즉시 튜토리얼을 하향한다.')]:
            result=evaluate_report(provider,doc['task'],{'version':1,'text':text},[],doc['executions'],[])
            audit['probes'].append(dict(name=name,text=text,result=result))
            save()
    # Export only generated synthetic records; credentials never enter the output.
    save()


if __name__=='__main__': main()
