"""Spec035 verification against an explicitly disposable PostgreSQL cluster.

Run with --port for the da-agent-spec035-test-db container, never production DBs.
--live uses the given key file and a Gemma model for direct synthetic generation.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/automated')]
import psycopg
from da_agent.discord_store import DiscordStore
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_query import DiscordQueryEngine
from da_agent.sql_runner import SqlRunner
from da_agent.discord_provider import DiscordGemmaProvider
from test_adaptive_tasks import bot_recipe


class FixtureProvider:
    model='spec035-fixture'
    generation_mode='synthetic'
    def review(self,messages):
        # This provider is only a deterministic integration fixture.
        text=messages[0]['content']
        if '독립 검증자' in text:
            value={'aligned':True,'issues':[],'quality_dimensions':{k:'pass' for k in ('business_context','evidence_sufficiency','difficulty_fit','evaluation_alignment')}}
        elif '집계 조건' in text:
            value={'state':'ready','conditions':{'table':'activity_daily','operation':'avg','column':'actions',
                'joins':[{'table':'accounts','source_column':'account_id'}],'group_by':['accounts.platform']}}
        elif 'criteria' in text and ('평가' in text or 'rubric' in text):
            value={'criteria':[{'id':cid,'grade':3,'reason':'저장 근거와 공개 조건을 연결','improvement':'추가 관측의 범위를 구체화',
                'evidence_refs':['report:1'],'deductions':[]} for cid in
                ('problem_definition','metric_design','hypothesis_review','evidence_interpretation','decision_limits')]}
        else: value=bot_recipe()
        return {'state':'completed','text':json.dumps(value,ensure_ascii=False),'model':self.model}


def settings_for(port,directory):
    # All credentials are for a throwaway container, not user secrets.
    base=f'postgresql://postgres:spec035-test-only@127.0.0.1:{port}/'
    with psycopg.connect(base+'postgres',autocommit=True,connect_timeout=5) as conn:
        for database in ('spec035_records','spec035_data'):
            if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(database,)).fetchone():
                conn.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(database)))
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='spec035_reader'").fetchone():
            conn.execute("CREATE ROLE spec035_reader LOGIN PASSWORD 'spec035-read-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
    return NS(records_dsn=base+'spec035_records',admin_dsn=base+'spec035_data',
        learner_dsn=f'postgresql://spec035_reader:spec035-read-only@127.0.0.1:{port}/spec035_data',
        generation_directory=str(directory/'packages'),quality_profiles_directory=str(directory/'profiles'),
        daily_call_limit=30,max_rows=1000,max_bytes=1048576,query_timeout_ms=5000,preview_rows=200,execution_ttl=600)


def create_service(settings,provider):
    store=DiscordStore(settings.records_dsn); store.initialize()
    return DiscordTrainingService(store,DiscordQueryEngine(provider,SqlRunner(settings),settings),provider,settings)


def scenario(service,*,live=False,text=None,difficulty='intermediate',retry_result=None):
    if retry_result:
        owner=retry_result['owner']; sid=retry_result['session_id']
        session=service.store.get(owner,sid)
    else:
        owner='spec035-'+uuid.uuid4().hex
        session=service.start(owner,'spec035-guild','spec035-parent',uuid.uuid4().hex,
            text=text or '반복 행동 계정의 활동량과 정상 반례를 비교하고 싶어',difficulty=difficulty)
        sid=session['session_id']
    if not live:
        service.store.save_generation_job(owner,sid,{'source_case':{'topic':'반복 행동','sources':[]}})
    result=service.generate(owner,sid,retry=bool(retry_result))
    evidence={'owner':owner,'session_id':sid,'state':result['state'],'generation':result['generation'],
        'task':result['task'],'live_model':live}
    if result['state']!='analysis': return evidence
    if not live:
        outcome=service.handle(owner,sid,uuid.uuid4().hex,'query','플랫폼별 평균 활동량을 비교해줘')
        assert outcome['session']['executions'],outcome
        execution=outcome['session']['executions'][-1]
        assert execution['result']['result_complete'] and len(execution['result']['rows'])==2
        evidence['query']=execution
        service.handle(owner,sid,uuid.uuid4().hex,'evidence',payload={'execution_id':execution['execution_id']})
        service.handle(owner,sid,uuid.uuid4().hex,'report','플랫폼별 활동량을 비교했다. 원인은 관측만으로 확정할 수 없고 추가 확인이 필요하다.')
        service.handle(owner,sid,uuid.uuid4().hex,'followup','동일 관측 기간에서 정상 고빈도 이용자 반례와 수집 상태를 확인한다.')
        submitted=service.handle(owner,sid,uuid.uuid4().hex,'submit')
        evidence['submission']=submitted['session']['evaluations'][-1]['result']
        assert evidence['submission']['held'] and evidence['submission']['total'] is None
        assert evidence['submission'].get('profile_score_hold'),evidence['submission']
    restored=create_service(service.settings,service.provider).resume(owner,'spec035-guild',sid)['session']
    assert restored['task']==result['task'] and restored['schema_name']==result['schema_name']
    evidence['restart_restored']=True
    with psycopg.connect(service.settings.learner_dsn) as conn:
        try:
            conn.execute(psycopg.sql.SQL('INSERT INTO {}.{} DEFAULT VALUES').format(
                psycopg.sql.Identifier(result['schema_name']),psycopg.sql.Identifier(next(iter(result['task']['schema'])))))
        except psycopg.errors.InsufficientPrivilege:
            evidence['learner_write_blocked']=True
    assert evidence.get('learner_write_blocked')
    return evidence


def preview_app(settings):
    """Local verification surface; not a replacement for live Discord testing."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse,JSONResponse
    from da_agent.discord_presentation import task_intro
    from da_agent.errors import DomainError
    app=FastAPI()
    service=create_service(settings,FixtureProvider())
    # A fixed fixture cannot redesign around a previous run's task; isolate
    # each preview run while retaining reload/resume within this process.
    owner='spec035-screen-'+uuid.uuid4().hex
    @app.exception_handler(DomainError)
    async def error(_,exc): return JSONResponse({'error':exc.message},status_code=400)
    @app.get('/')
    def page(): return HTMLResponse((ROOT/'tests/artifacts/spec035-2026-10-06/preview.html').read_text(encoding='utf-8'))
    def response(doc,messages):
        return {'session_id':doc['session_id'],'state':doc['state'],'messages':messages,
            'executions':doc['executions'],'reports':doc['reports'],'evaluations':doc['evaluations']}
    @app.post('/training')
    def training(body:dict):
        doc=service.start(owner,'spec035-guild','spec035-parent',uuid.uuid4().hex,text=body.get('text',''),difficulty=body.get('difficulty','intermediate'))
        service.store.save_generation_job(owner,doc['session_id'],{'source_case':{'topic':'반복 행동','sources':[]}})
        doc=service.generate(owner,doc['session_id'])
        return response(doc,service.resume(owner,'spec035-guild',doc['session_id'])['messages'])
    @app.post('/action')
    def action(body:dict):
        value=service.handle(owner,body['session_id'],uuid.uuid4().hex,body['action'],body.get('text',''))
        return response(value['session'],value['messages'])
    @app.get('/session/{sid}')
    def resume(sid:str):
        value=service.resume(owner,'spec035-guild',sid)
        messages=list(value['messages'])
        if value['session']['evaluations']: messages += ['저장된 평가: '+str(value['session']['evaluations'][-1]['result']['reason'])]
        return response(value['session'],messages)
    return app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',required=True,type=int)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--key-file')
    parser.add_argument('--model', default='gemma-4-26b-a4b-it')
    parser.add_argument('--diagnostics-dir', help='Private ignored directory for invalid JSON response originals')
    parser.add_argument('--retry-result', help='Retry an existing failed local verification session result')
    parser.add_argument('--text')
    parser.add_argument('--difficulty',default='intermediate')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--http-port',type=int,default=8096)
    parser.add_argument('--output',default='tests/artifacts/spec035-2026-10-06')
    args=parser.parse_args()
    directory=ROOT/args.output; directory.mkdir(parents=True,exist_ok=True)
    settings=settings_for(args.port,directory)
    if args.serve:
        import uvicorn
        uvicorn.run(preview_app(settings),host='127.0.0.1',port=args.http_port)
        return
    if args.live:
        if not args.key_file: parser.error('--live requires explicit --key-file')
        provider=DiscordGemmaProvider(key_file=args.key_file, model=args.model, diagnostics_directory=args.diagnostics_dir)
    else: provider=FixtureProvider()
    retry_result=json.loads(Path(args.retry_result).read_text(encoding='utf-8')) if args.retry_result else None
    result=scenario(create_service(settings,provider),live=args.live,text=args.text,difficulty=args.difficulty,retry_result=retry_result)
    file=directory/('live-'+uuid.uuid4().hex[:8]+'.json' if args.live else 'integration.json')
    file.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'state':result['state'],'file':str(file),'calls':result['generation'].get('planning_calls'),
        'restart_restored':result.get('restart_restored'),'error_code':result['generation'].get('error_code')},ensure_ascii=False))
    if result['state']!='analysis': sys.exit(1)


if __name__=='__main__': main()
