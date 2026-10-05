"""Reproducible Spec013 checks. Never print credentials or raw pytest failures.

Default: all tests with DB tests disabled. --db delegates to the existing
disposable-record-schema runner. --docker NAME delegates to that runner inside
the named, already running container. No volume reset or live AI call occurs.
"""
import argparse
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PENDING = {
    'human_semantic_review': 'pending: named reviewer, sample IDs, version, verdict and time required',
    'current_model_repetitions': 'pending: actual model, each fixed sample 3 times, human review and score range <=10',
    'other_pc': 'pending: second machine request, actual SQL, review and resume evidence',
    'arm': 'pending: separate ARM environment and result; absence is not support verification',
    'five_learner_pilot': 'pending: 5 actual learners; independent completion >=4 and core-condition improvement >=3',
    'actual_browser': 'pending: browser evidence is recorded separately, never inferred from pytest',
}


def run(command, env=None):
    try:
        return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired):
        return None


def versions():
    result = {}
    for path in sorted((ROOT / 'src' / 'da_agent').glob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError):
            continue
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name) and (target.id == 'VERSION' or target.id.endswith('_VERSION')):
                        found[target.id] = node.value.value
        if found:
            result[path.name] = found
    return result


def snapshot():
    revision = run(['git', 'rev-parse', 'HEAD'])
    dirty = run(['git', 'status', '--porcelain'])
    docker = run(['docker', 'version', '--format', '{{json .}}'])
    docker_meta = {'availability': 'unavailable'}
    if docker and docker.returncode == 0:
        try:
            data = json.loads(docker.stdout)
            docker_meta = {side.lower(): {key: data.get(side, {}).get(key) for key in ('Version', 'Os', 'Arch')} for side in ('Client', 'Server')}
        except (ValueError, AttributeError):
            pass
    database = {'availability': 'not_configured', 'scope': 'host snapshot; Docker test environment is separate'}
    if os.getenv('RECORDS_DSN'):
        try:
            import psycopg
            with psycopg.connect(os.environ['RECORDS_DSN'], connect_timeout=5) as conn:
                database = {'server_version': conn.info.server_version, 'role': 'records', 'availability': 'connected'}
        except Exception:
            database = {'availability': 'unavailable', 'role': 'records'}
    sha = revision.stdout.strip() if revision and revision.returncode == 0 else 'unavailable'
    source_hashes = {str(path.relative_to(ROOT / 'src' / 'da_agent')).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sorted((ROOT / 'src' / 'da_agent').rglob('*')) if path.is_file() and path.suffix in {'.py', '.js', '.css', '.html'}}
    return {'executed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'display_timezone': 'Asia/Seoul',
            'code_revision': sha, 'working_tree_dirty': bool(dirty and dirty.stdout),
            'environment': {'os': platform.system(), 'os_release': platform.release(), 'cpu_architecture': platform.machine(),
                            'python': platform.python_version(), 'docker': docker_meta, 'database': database},
            'source_versions': versions(), 'source_file_sha256': source_hashes, 'model_observation': 'not_called; configured model is not proof of actual model execution',
            'material_policy': 'No environment dump, DSN, key, SQL, rows, conversation or provider-response output',
            'release_approval': 'pending', 'external_validation': PENDING}


def summarize(process):
    if process is None:
        return {'status': 'failed', 'failure_class': 'process_unavailable_or_timeout'}
    counts = {}
    # Emit only numeric test summary tokens, not traceback/SQL/connection details.
    for count, kind in re.findall(r'\b(\d+) (passed|failed|skipped|errors?|warnings?|deselected)\b', process.stdout):
        counts[kind] = int(count)
    return {'status': 'passed' if process.returncode == 0 else 'failed', 'exit_code': process.returncode,
            'counts': counts, 'raw_output': 'not_recorded'}


def container_snapshot(name):
    code = '''import hashlib,json,os,platform
from pathlib import Path
import da_agent
root=Path(da_agent.__file__).resolve().parent
data={'os':platform.system(),'cpu_architecture':platform.machine(),'python':platform.python_version()}
data['source_file_sha256']={str(p.relative_to(root)).replace(chr(92),'/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file() and p.suffix in {'.py','.js','.css','.html'}}
data['generator_stage_environment_present']=bool(os.getenv('GENERATOR_DSN') or Path(os.getenv('GENERATOR_PASSWORD_FILE','/run/secrets/generator_password')).is_file())
try:
 import psycopg
 with psycopg.connect(os.environ['RECORDS_DSN'],connect_timeout=5) as c: data['records_server_version']=c.info.server_version
except Exception: data['records_server_version']='unavailable'
print(json.dumps(data))'''
    process = run(['docker', 'exec', name, 'python', '-c', code])
    if process and process.returncode == 0:
        try:
            return json.loads(process.stdout)
        except ValueError:
            pass
    return {'availability': 'unavailable'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--db', action='store_true', help='Full suite in a disposable recorder schema using RECORDS_DSN')
    modes.add_argument('--docker', metavar='CONTAINER', help='Full disposable-schema suite in an existing container')
    modes.add_argument('--snapshot-only', action='store_true', help='Record versions/environment without starting tests')
    parser.add_argument('--tests', default='tests', help='Host tests path; with --docker, an existing container tests path')
    parser.add_argument('--output', type=Path, help='Save safe JSON evidence; default prints safe JSON')
    args = parser.parse_args(argv)
    record = snapshot()
    record['test_material'] = {'database': 'actual PostgreSQL only in --db/--docker mode',
                               'provider': 'fixture/mocked where tests configure it; pytest does not approve current-model quality',
                               'learners': 'test fixtures, never pilot participants'}
    status = 0
    if args.snapshot_only:
        record['automatic_checks'] = {'status': 'not_run'}
    else:
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src') + os.pathsep + os.environ.get('PYTHONPATH', ''))
        if args.docker:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', args.docker):
                parser.error('container must be a literal Docker container name')
            command = ['docker', 'exec', args.docker, 'python', 'scripts/verification/verify_request_training.py', args.tests]
            metadata = container_snapshot(args.docker)
            record['test_environment'] = {'kind': 'existing Docker container', 'container': args.docker, 'snapshot': metadata,
                                          'source_matches_host': metadata.get('source_file_sha256') == record['source_file_sha256']}
        elif args.db:
            if not os.getenv('RECORDS_DSN'):
                parser.error('--db requires RECORDS_DSN; credentials are not printed')
            command = [sys.executable, str(ROOT / 'scripts' / 'verification' / 'verify_request_training.py'), args.tests]
            record['test_environment'] = {'kind': 'host with disposable recorder schema'}
        else:
            env['RUN_DB_TESTS'] = '0'
            command = [sys.executable, '-m', 'pytest', '-q', args.tests]
            record['test_environment'] = {'kind': 'host; DB tests disabled explicitly'}
        record['automatic_checks'] = summarize(run(command, env))
        record['automatic_checks']['runner'] = 'verify_request_training.py' if args.db or args.docker else 'pytest -q'
        record['automatic_checks']['generator_stage_environment_present'] = bool(os.getenv('GENERATOR_DSN')) if not args.docker else 'container environment; inspect skipped count separately'
        status = 0 if record['automatic_checks']['status'] == 'passed' else 1
    content = json.dumps(record, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + '\n', encoding='utf-8')
    else:
        print(content)
    return status


if __name__ == '__main__':
    sys.exit(main())
