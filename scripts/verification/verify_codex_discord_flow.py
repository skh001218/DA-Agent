"""Real subscription calls against a disposable PostgreSQL fixture, never the running bot."""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from verify_discord_text_generation import create_service
from da_agent.codex_provider import CodexCliProvider


def settings_for(port, directory):
    base = f'postgresql://postgres:spec048-test-only@127.0.0.1:{port}/'
    with psycopg.connect(base+'postgres', autocommit=True) as conn:
        for name in ('spec048_records', 'spec048_training'):
            if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone():
                conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='spec048_reader'").fetchone():
            conn.execute("CREATE ROLE spec048_reader LOGIN PASSWORD 'spec048-reader-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
    return SimpleNamespace(records_dsn=base+'spec048_records', admin_dsn=base+'spec048_training',
        learner_dsn=f'postgresql://spec048_reader:spec048-reader-only@127.0.0.1:{port}/spec048_training',
        generation_directory=str(directory/'packages'), quality_profiles_directory=str(directory/'profiles'),
        daily_call_limit=30, max_rows=1000, max_bytes=1048576, query_timeout_ms=5000,
        preview_rows=200, execution_ttl=600)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True, type=int, help='Disposable fixture DB only')
    parser.add_argument('--home')
    parser.add_argument('--executable', default='codex')
    parser.add_argument('--model')
    parser.add_argument('--text', default='플랫폼별 신규 계정의 일일 활동 횟수를 비교하고 운영 우선순위를 정하고 싶어')
    parser.add_argument('--difficulty', choices=['beginner','intermediate','advanced'], default='intermediate')
    parser.add_argument('--output', default='.local/spec048/flow.json')
    parser.add_argument('--resume', action='store_true', help='Reuse this fixture session; failed generation is manually retried')
    parser.add_argument('--live', required=True, action='store_true', help='Consumes subscription usage')
    args = parser.parse_args()
    directory = (ROOT / args.output).resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    settings = settings_for(args.port, directory)
    provider = CodexCliProvider(executable=args.executable, home=args.home, model=args.model)
    service = create_service(settings, provider)
    if args.resume:
        saved = json.loads((ROOT / args.output).read_text(encoding='utf-8'))
        owner, sid = saved['owner'], saved['session_id']
        if not owner.startswith('spec048-'):
            parser.error('Only this verification fixture owner may be resumed')
        session = service.store.get(owner, sid)
    else:
        owner = 'spec048-' + uuid.uuid4().hex
        session = service.start(owner, 'spec048-guild', 'spec048-parent', uuid.uuid4().hex,
                                text=args.text, difficulty=args.difficulty, practice='analysis')
        sid = session['session_id']
    evidence = {'session_id': sid, 'owner': owner, 'provider': 'codex_cli', 'phases': []}
    def save(phase, passed, **details):
        evidence['phases'].append(dict(phase=phase, passed=passed, **details))
        (ROOT / args.output).write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(json.dumps(evidence['phases'][-1], ensure_ascii=False), flush=True)
        if not passed:
            raise SystemExit(1)
    session = service.generate(owner, sid, retry=args.resume)
    save('generation', session['state'] == 'analysis',
         error_code=session['generation'].get('error_code'), calls=session['generation'].get('planning_calls'))
    task = session['task']
    evidence['task'] = task
    evidence['generation_calls'] = service.store.generation_job(owner, sid).get('calls', [])
    metric_name, metric = next(iter(task['metrics'].items()))
    query = '공개 지표 ' + metric_name + '의 정의 그대로 조회해줘. 조건: ' + json.dumps(metric, ensure_ascii=False)
    if args.resume and session.get('executions'):
        outcome = {'session': session, 'messages': ['저장된 성공 조회를 재사용했습니다.']}
    else:
        outcome = service.handle(owner, sid, uuid.uuid4().hex, 'query', query)
    executions = outcome['session'].get('executions', [])
    save('query', bool(executions) and executions[-1]['status'] == 'success',
         messages=outcome.get('messages'))
    execution = executions[-1]
    evidence['query'] = execution
    service.handle(owner, sid, uuid.uuid4().hex, 'evidence', payload={'execution_id': execution['execution_id']})
    calls_before = len(service.store.get(owner, sid)['telemetry'])
    help_result = service.handle(owner, sid, uuid.uuid4().hex, 'question', '선택 편향이 뭐야?')
    telemetry = help_result['session']['telemetry'][calls_before:]
    save('education', bool(telemetry) and telemetry[-1].get('state') == 'completed',
         messages=help_result.get('messages'))
    report = ('공개 과제의 대상과 관측 기간을 기준으로 공개 지표 ' + metric_name + '를 비교했다. '
              '공개 표의 행 단위, 집계 대상, 조건과 비교 집단을 지표 정의 그대로 적용했다: '
              + json.dumps(metric, ensure_ascii=False) + '. 저장 실행 결과: '
              + json.dumps(execution['result']['rows'], ensure_ascii=False)
              + '. 이 차이만으로 가입 경로의 인과 효과는 확정할 수 없다. 수집 누락과 이용자 구성 차이를 점검하고 '
                '같은 관측 조건으로 추가 비교한 뒤 운영 행동을 결정하겠다.')
    service.handle(owner, sid, uuid.uuid4().hex, 'report', report)
    service.handle(owner, sid, uuid.uuid4().hex, 'followup', '동일한 관측 기간과 이용자 조건에서 재확인하고 누락 자료를 점검한다.')
    submitted = service.handle(owner, sid, uuid.uuid4().hex, 'submit')
    evaluation = submitted['session']['evaluations'][-1]['result']
    save('evaluation', evaluation.get('quality_validation', {}).get('status') == 'passed'
         and not evaluation.get('provider_failure'),
         held=evaluation.get('held'), total=evaluation.get('total'),
         quality_status=evaluation.get('quality_validation', {}).get('status'))
    evidence['evaluation'] = evaluation
    restored = create_service(settings, provider).resume(owner, 'spec048-guild', sid)['session']
    save('resume', restored['task'] == task and restored['evaluations'] == submitted['session']['evaluations'])
    print(json.dumps({'state': 'verified', 'file': str(ROOT / args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
