"""SQL generation/retention QA in an explicit disposable DB; never sends Discord."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/automated')]
import psycopg
from psycopg import sql
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore
from da_agent.discord_presentation import task_intro
from da_agent.sql_runner import SqlRunner
from da_agent.adaptive_tasks import Recipe
from da_agent.discord_generated_sql import prepare_checks
from da_agent.packages import PackageCatalog
from test_discord_generated_sql import RetentionProvider

LEVELS=('beginner','intermediate','advanced')


def settings_for(port,directory, *, live=False):
    # The password is only valid for the named spec057 disposable container.
    base=f'postgresql://postgres:spec057-test-only@127.0.0.1:{port}/'
    suffix='057_live' if live else '057'
    with psycopg.connect(base+'postgres',autocommit=True,connect_timeout=5) as conn:
        for name in ('discord_test_sql'+suffix,'discord_test_records'+suffix):
            if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
                conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='sql057_reader'").fetchone():
            conn.execute("CREATE ROLE sql057_reader LOGIN PASSWORD 'sql057-read-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
    return NS(admin_dsn=base+'discord_test_sql'+suffix,records_dsn=base+'discord_test_records'+suffix,
        learner_dsn=f'postgresql://sql057_reader:sql057-read-only@127.0.0.1:{port}/discord_test_sql'+suffix,
        generation_directory=str(directory/'packages'),daily_call_limit=0,query_timeout_ms=5000,
        max_rows=1000,max_bytes=1048576,preview_rows=10,execution_ttl=600)


def create_service(settings,provider):
    store=DiscordStore(settings.records_dsn); store.initialize()
    return DiscordTrainingService(store,NS(runner=SqlRunner(settings)),provider,settings)


def run(settings,output,provider_factory, *, resume=False, levels=LEVELS):
    results=[json.loads((output/f'{level}.json').read_text(encoding='utf-8')) for level in LEVELS if level not in levels and (output/f'{level}.json').exists()]
    for level in levels:
        service=create_service(settings,provider_factory(level))
        owner='sql057-'+uuid4().hex
        dimension={'beginner':'전체','intermediate':'유입 채널별','advanced':'가입 코호트와 유입 채널별'}[level]
        text=f'게임 신규 이용자의 D7 리텐션을 {dimension} 계산하는 SQL 문제를 만들어줘. 무접속자 포함, 관측 미완료 이용자는 분모 제외. 이용자별 관측 요약 자료를 제공해줘.'
        saved=None
        if resume:
            with psycopg.connect(settings.records_dsn) as conn:
                saved=conn.execute("SELECT document FROM discord_records.sessions WHERE guild_id='sql057-test' AND document->>'difficulty'=%s ORDER BY updated_at DESC LIMIT 1",(level,)).fetchone()
        if saved:
            doc=saved[0]; owner=doc['owner_user_id']; text=doc['generation']['original_message']
        else:
            doc=service.start(owner,'sql057-test','parent',uuid4().hex,text=text,practice='sql',difficulty=level)
        doc=service.generate(owner,doc['session_id'],retry=bool(saved))
        report=dict(level=level,owner=owner,session_id=doc['session_id'],generation=doc['generation'],request=text)
        if doc['generation']['status']=='ready':
            sid=doc['session_id']
            job=service.store.generation_job(owner,sid)
            package=PackageCatalog(settings.generation_directory).load(job['package_id'],'v1')
            # Replayed live QA also verifies the current case-generation code.
            recipe=Recipe.model_validate(job['recipe'])
            reference=prepare_checks(settings,package,recipe,int(hashlib.sha256(sid.encode()).hexdigest()[:8],16))
            job['sql_reference']=reference; service.store.save_generation_job(owner,sid,job)
            report.update(task=doc['task'],intro=task_intro(doc))
            metric=doc['task']['sql_contract']['metric']
            filters='; '.join(c['column']+' '+c['operator']+' '+str(c['value']) for c in metric['denominator_conditions'])
            groups=', '.join(metric['group_by']) or '전체'
            keys=[metric['table']+'.'+recipe.tables[[t.name for t in recipe.tables].index(metric['table'])].columns[0].name]
            for join in metric['joins']:
                keys.extend([metric['table']+'.'+join['source_column'],join['table']+'.'+next(t for t in recipe.tables if t.name==join['table']).columns[0].name])
            empty_plan=('빈 표의 이 GROUP BY 쿼리는 0행이며 분모 조건을 만족하는 행이 없는 그룹도 미출력인지 확인합니다. '
                '별도로 GROUP BY를 제거한 전체 집계에서 denominator/numerator=0, NULLIF로 비율 NULL인지 검산합니다. '
                if metric['group_by'] else '빈 표의 전체 집계에서 denominator/numerator=0, NULLIF로 비율 NULL인지 확인합니다. ')
            notes=('\n검산 계획: '+', '.join(keys)+' 각각으로 GROUP BY하여 COUNT(*)=1인지 확인합니다. '
                '관측 요약은 계정당 한 행인지, FK 조인 전후 COUNT(*)가 같고 조인 후에도 계정키별 COUNT(*)=1인지 비교해 중복 증폭을 점검합니다. '
                '기간/관측: '+filters+'의 각 경계 바로 전·같은 값·바로 뒤를 비교합니다. gte 경계값은 포함, lt 경계값은 제외하고 '
                '가입 날짜 및 D7 날짜는 Asia/Seoul 기준으로 변환하여 관측 일수 6/7과 연결합니다. '
                +groups+'별 분모/분자를 별도 집계해 그룹 누락과 다른 그룹 혼합이 없는지 대조합니다. '
                '분모: 무접속자도 관측 완료 시 포함하고 미관측자는 제외되는지 확인합니다. '
                +empty_plan+'모두 아직 수행하지 않은 계획입니다.')
            query='WITH answer AS ('+reference['sql']+') SELECT * FROM answer'
            answer=service.handle(owner,sid,uuid4().hex,'sqlrun','```sql\n'+query+'\n```'+notes)
            attempt=answer['session']['sql_attempts'][-1]
            submitted=service.handle(owner,sid,uuid4().hex,'submit')
            evaluation=submitted['session']['evaluations'][-1]['result']
            restored=create_service(settings,service.provider).resume(owner,'sql057-test',sid)['session']
            report.update(execution=attempt,evaluation=evaluation,restart_restored=restored['sql_attempts'][-1]['sql']==query,
                submission=submitted['submission'],passed=not evaluation['held'] and all(c['status']=='충족' for c in evaluation['criteria']))
            report['verification_population_varied']=any(c['expected']!=reference['checks'][0]['expected'] for c in reference['checks'][1:3])
            # Private metadata stays in local generation records, never reports.
            assert all(c['schema_name'] not in json.dumps(submitted['submission']) for c in reference['checks'])
        else:
            report['passed']=False
        results.append(report)
        (output/f'{level}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(level+': '+doc['generation']['status']+'; passed='+str(report['passed']),flush=True)
    (output/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return results


def preview_app(settings,output):
    """A local QA view of saved SQL tasks using the real service actions."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel
    app=FastAPI()
    reports={r['level']:r for r in json.loads((output/'summary.json').read_text(encoding='utf-8'))}
    page=ROOT/'tests/manual/text-sql-retention-preview.html'
    class Action(BaseModel):
        action:str
        text:str=''
    @app.get('/')
    def index(): return HTMLResponse(page.read_text(encoding='utf-8'))
    @app.get('/task/{level}')
    def task(level:str):
        r=reports[level]; service=create_service(settings,RetentionProvider(level))
        doc=service.store.get(r['owner'],r['session_id'])
        return dict(intro=task_intro(doc),schema=doc['task']['dictionary'],attempts=doc['sql_attempts'],evaluations=doc['evaluations'])
    @app.post('/task/{level}')
    def action(level:str,value:Action):
        r=reports[level]; service=create_service(settings,RetentionProvider(level))
        response=service.handle(r['owner'],r['session_id'],uuid4().hex,value.action,value.text)
        return dict(messages=response['messages'],attempts=response['session']['sql_attempts'],evaluations=response['session']['evaluations'])
    @app.post('/generate/{level}')
    def generate(level:str,value:Action):
        owner='sql057-screen-'+uuid4().hex
        service=create_service(settings,RetentionProvider(level))
        doc=service.start(owner,'sql057-test','parent',uuid4().hex,text=value.text,practice='sql',difficulty=level)
        doc=service.generate(owner,doc['session_id'])
        reports[level]=dict(owner=owner,session_id=doc['session_id'])
        return dict(status=doc['generation']['status'],intro=task_intro(doc),schema=doc['task']['dictionary'])
    return app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=55457)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--levels',nargs='+',choices=LEVELS,default=LEVELS)
    parser.add_argument('--serve',action='store_true')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    # Private generator recipes/packages are ignored and never exported.
    settings=settings_for(args.port,ROOT/'.local/sql057-validation',live=args.live)
    if args.serve:
        import uvicorn
        uvicorn.run(preview_app(settings,args.output),host='127.0.0.1',port=8057)
        return
    if args.live:
        from da_agent.codex_provider import CodexCliProvider
        factory=lambda level:CodexCliProvider(timeout=180)
    else: factory=RetentionProvider
    result=run(settings,args.output,factory,resume=args.resume,levels=args.levels)
    if not all(r['passed'] for r in result): raise SystemExit(1)


if __name__=='__main__': main()
