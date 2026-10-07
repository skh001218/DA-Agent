"""Review Spec035/036 in the explicit disposable spec035 PostgreSQL cluster.

No Discord messages or production changes. Key content is never exported.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests/automated'), str(Path(__file__).parent)]
from verify_discord_text_generation import settings_for, create_service, FixtureProvider
from da_agent.discord_sql_practice import reference_sql
from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.adaptive_tasks import Recipe
from da_agent.task_quality import check_quality, structure
from da_agent.errors import DomainError
from test_adaptive_tasks import bot_recipe

LEVELS = ('beginner', 'intermediate', 'advanced')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--budget-dir', default=str(ROOT/'.local/spec035-036-validation/api-budget'))
    parser.add_argument('--key-file')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--live-request', help='Run only this exact request')
    parser.add_argument('--live-difficulties', nargs='+', choices=LEVELS, default=LEVELS)
    parser.add_argument('--db-tests', action='store_true')
    args = parser.parse_args()
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    settings = settings_for(args.port, out)
    if args.db_tests:
        # The legacy query suite additionally enforces a discord_test DB name.
        with psycopg.connect(settings.admin_dsn, autocommit=True) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname='discord_test_audit'").fetchone():
                conn.execute('CREATE DATABASE discord_test_audit')
        data_dsn = settings.admin_dsn.rsplit('/',1)[0] + '/discord_test_audit'
        learner_dsn = settings.learner_dsn.rsplit('/',1)[0] + '/discord_test_audit'
        env = dict(os.environ, DISCORD_TEST_ADMIN_DSN=data_dsn,
                   DISCORD_TEST_LEARNER_DSN=learner_dsn,
                   DISCORD_TEST_RECORDS_DSN=settings.records_dsn,
                   DISCORD_TEST_DSN=data_dsn)
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
            *[str(ROOT/'tests/automated'/f'test_discord_{name}.py') for name in
              ('sql_practice','generation','service','flow','query','education','transport','provider')],
            f'--junitxml={out / "db-tests.xml"}'], env=env, cwd=ROOT)
        if result.returncode:
            raise RuntimeError('DB regression failed; inspect db-tests.xml')
    service = create_service(settings, FixtureProvider())
    sql_results = []
    for level in LEVELS:
        owner = 'audit-sql-' + uuid4().hex
        doc = service.start(owner, 'audit-guild', 'audit-parent', uuid4().hex,
            practice='sql', text='튜토리얼 신규 가입자 3단계 완료율',
            difficulty=level, help_level='independent')
        sid = doc['session_id']
        response = service.handle(owner, sid, uuid4().hex, 'sqlrun',
            '```sql\n' + reference_sql(doc['task']) + '\n```')
        attempt = response['session']['sql_attempts'][-1]
        submitted = service.handle(owner, sid, uuid4().hex, 'submit')
        record = {'difficulty':level,'help_level':doc['help_level'],
            'objective':doc['task']['objective'], 'contract':doc['task']['sql_contract'],
            'main_data_hash':hashlib.sha256(json.dumps(__import__('da_agent.discord_sql_practice',fromlist=['fixture_rows']).fixture_rows(doc['task'],'main'),default=str,sort_keys=True).encode()).hexdigest(),
            'result':attempt['full_result'], 'evaluation':submitted['session']['evaluations'][-1]['result'],
            'completed':submitted['submission']['completed'],
            'attempt_count':len(submitted['session']['sql_attempts']),
            'no_written_explanation':not bool(submitted['session']['reports'])}
        sql_results.append(record)
        write(out / 'sql-levels.json', sql_results)
        print(json.dumps({'sql_level':level,'completed':record['completed'],
            'rows':len(record['result']['rows'])}), flush=True)
    # Demonstrate semantic gaps in structural quality checks, without claiming
    # these crafted fixtures are real model outputs.
    value = bot_recipe()
    req = value['business_case']['requirements'][0]
    crafted = deepcopy(value)
    crafted['difficulty']='advanced'
    for competency in ('alternatives','confounding'):
        item=deepcopy(req)
        item.update(competency=competency, question='플랫폼별 평균 활동량만 계산하세요. 추가 판단은 필요하지 않습니다.',
                    completion='플랫폼별 평균 활동량 표 한 개를 제시하면 완료합니다.')
        crafted['business_case']['requirements'].append(item)
    try:
        check_quality(Recipe.model_validate(crafted))
        cosmetic_accepted=True
    except (DomainError, ValueError):
        cosmetic_accepted=False
    assert not cosmetic_accepted
    probes={'cosmetic_advanced_accepted':cosmetic_accepted,
        'intermediate_metric_structure':structure(Recipe.model_validate(value)),
        'advanced_metric_structure':structure(Recipe.model_validate(crafted))}
    owner='audit-contract-'+uuid4().hex
    try:
        service.start(owner,'audit-guild','audit-parent',uuid4().hex,text='계정 행동 비교',difficulty='intermediate')
        raise AssertionError('missing practice accepted')
    except DomainError as exc:
        probes['analysis_without_practice_error']=exc.code
    try:
        service.start(owner,'audit-guild','audit-parent',uuid4().hex,practice='sql',
            text='튜토리얼 완료율 대신 D7 리텐션을 계산해줘',difficulty='intermediate')
        raise AssertionError('unsupported SQL substituted')
    except DomainError as exc:
        probes['unsupported_sql_error']=exc.code
    write(out / 'contract-probes.json', probes)
    if args.live:
        if not args.key_file: parser.error('--live requires --key-file')
        base=settings.records_dsn.rsplit('/',1)[0]
        with psycopg.connect(base+'/postgres',autocommit=True) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname='spec035_live_records'").fetchone():
                conn.execute('CREATE DATABASE spec035_live_records')
        settings.records_dsn=base+'/spec035_live_records'
        provider = DiscordGemmaProvider(key_file=args.key_file, model='gemma-4-26b-a4b-it',budget_directory=args.budget_dir)
        live_service = create_service(settings, provider)
        records=[]
        requests = [args.live_request] if args.live_request else (
            '튜토리얼 완료율 하락을 분석하고 싶어',
            '반복 행동 계정의 활동량과 정상 반례를 비교하고 싶어')
        for text in requests:
            for level in args.live_difficulties:
                owner='audit-live-'+uuid4().hex
                doc=live_service.start(owner,'audit-guild','audit-parent',uuid4().hex,
                    practice='analysis',text=text,difficulty=level,help_level='independent')
                result=live_service.generate(owner,doc['session_id'])
                job=live_service.store.generation_job(owner,doc['session_id'])
                # Export public task and metadata; private drafts/answers stay in DB.
                record={'request':text,'difficulty':level,'help_level':'independent',
                    'session_id':doc['session_id'],'state':result['state'],
                    'generation':result['generation'],'public_task':result['task'],
                    'calls':[{k:c[k] for k in ('phase','state','model','reason','usage','provider_diagnostic','quota_diagnostic','json_diagnostic') if k in c}
                             for c in job.get('calls',[])],
                    'validation_failures':job.get('failures',[]),
                    'rejected_design_issues':[r.get('issues',[]) for r in job.get('rejected_designs',[])],
                    'alignment_errors':job.get('alignment_errors',[])}
                if result['state']=='analysis':
                    restored=create_service(settings,provider).resume(owner,'audit-guild',doc['session_id'])['session']
                    record['restart_restored']=restored['task']==result['task']
                records.append(record)
                write(out / 'live-levels.json', records)
                print(json.dumps({'request':text,'difficulty':level,'state':result['state'],
                    'calls':result['generation'].get('planning_calls'),
                    'error_code':result['generation'].get('error_code')},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
