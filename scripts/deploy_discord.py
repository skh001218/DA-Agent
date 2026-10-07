"""Deploy an exact, CI-passed main commit to the existing local Discord bot.

Default is a read-only plan. --apply builds from git archive, preserves the
running bot's configuration, verifies startup and restores its image on failure.
Private runtime configuration stays under the ignored .local directory.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen


REQUIRED_CHECKS = {'automated-tests', 'discord-db-tests', 'discord-image'}


class DeploymentError(RuntimeError):
    pass


def command(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        # Docker inspect/compose and credential helpers can contain secrets.
        raise DeploymentError(f'{args[0]} {args[1]} failed (exit {result.returncode}); runtime was not certified')
    return result.stdout.strip()


def inspect_container(name):
    return json.loads(command('docker', 'inspect', name))[0]


def approved_checks(checks):
    latest = {}
    for check in checks:
        if check.get('app', {}).get('slug') == 'github-actions':
            name = check['name']
            if name not in latest or check['id'] > latest[name]['id']:
                latest[name] = check
    return all(name in latest and latest[name]['status'] == 'completed'
               and latest[name]['conclusion'] == 'success' for name in REQUIRED_CHECKS)


def ci_headers():
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'DA-Agent-release'}
    # Reuse the existing Git login in memory. Never prompt for a new login or
    # persist/print credentials; unauthenticated public reads remain supported.
    try:
        result = subprocess.run(['git', 'credential', 'fill'],
            input='protocol=https\nhost=github.com\n\n', capture_output=True,
            text=True, encoding='utf-8', timeout=10,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'})
        credential = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode == 0 and credential.get('password'):
            headers['Authorization'] = 'Bearer ' + credential['password']
    except (OSError, subprocess.TimeoutExpired):
        pass
    return headers


def require_ci(repository, revision):
    url = f'https://api.github.com/repos/{repository}/commits/{revision}/check-runs?per_page=100'
    request = Request(url, headers=ci_headers())
    try:
        with urlopen(request, timeout=30) as response:
            checks = json.load(response)['check_runs']
    except Exception as exc:
        raise DeploymentError('Could not verify GitHub CI; deployment stopped') from exc
    if not approved_checks(checks):
        raise DeploymentError('The selected main commit must pass automated-tests, discord-db-tests and discord-image')


def main_revision(root, requested):
    remote = command('git', 'remote', 'get-url', 'origin', cwd=root)
    match = re.fullmatch(r'https://github.com/([\w.-]+/[\w.-]+?)(?:\.git)?', remote)
    if not match:
        raise DeploymentError('Expected a GitHub HTTPS origin for commit and CI verification')
    command('git', 'fetch', 'origin', 'main', cwd=root)
    tip = command('git', 'rev-parse', 'origin/main', cwd=root)
    revision = requested or tip
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise DeploymentError('--commit requires a full 40-character main commit SHA')
    try:
        command('git', 'merge-base', '--is-ancestor', revision, tip, cwd=root)
    except DeploymentError as exc:
        raise DeploymentError('Feature-branch commits cannot be deployed; merge the PR into main first') from exc
    return match[1], revision


def host_bind_source(source, platform=None):
    # Docker Desktop reports Windows binds using its Linux VM path after recreate.
    if (platform or os.name) == 'nt':
        match = re.fullmatch(r'/run/desktop/mnt/host/([A-Za-z])/(.+)', source)
        if match:
            return match[1].upper() + ':/' + match[2]
    return source


def compose_spec(current, image):
    labels = current['Config']['Labels']
    if labels.get('com.docker.compose.service') != 'bot':
        raise DeploymentError('The target must be the existing Compose bot service')
    if current['HostConfig'].get('Privileged') or current['HostConfig'].get('NetworkMode') == 'host':
        raise DeploymentError('Custom privileged/host-network deployments need a reviewed adapter')
    # Compose interpolates dollar signs even in JSON; retain literal runtime values.
    service = dict(image=image, environment=[value.replace('$', '$$') for value in current['Config']['Env']], restart='unless-stopped',
                   volumes=[], networks={}, working_dir=current['Config'].get('WorkingDir') or '/app')
    spec = dict(services={'bot': service}, volumes={}, networks={})
    for i, mount in enumerate(current['Mounts']):
        item = dict(type=mount['Type'], target=mount['Destination'], read_only=not mount['RW'])
        if mount['Type'] == 'volume':
            key = f'volume{i}'
            spec['volumes'][key] = dict(external=True, name=mount['Name'])
            item['source'] = key
        elif mount['Type'] == 'bind':
            source = host_bind_source(mount['Source'])
            if not Path(source).exists():
                raise DeploymentError('An existing secret/file mount is unavailable; preserve or repair it before deployment')
            item['source'] = source
        else:
            raise DeploymentError('Unsupported mount type; deployment stopped')
        service['volumes'].append(item)
    for i, name in enumerate(current['NetworkSettings']['Networks']):
        key = f'network{i}'
        spec['networks'][key] = dict(external=True, name=name)
        service['networks'][key] = dict(aliases=['bot'])
    if current['HostConfig'].get('PortBindings'):
        raise DeploymentError('Unexpected published bot ports; deployment stopped')
    return spec


@contextmanager
def release_lock(directory):
    lock = directory / 'deploy.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise DeploymentError('Another deployment holds .local/releases/deploy.lock') from exc
    try:
        with os.fdopen(fd, 'w') as file:
            file.write(str(os.getpid()))
        yield
    finally:
        lock.unlink()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def ensure_idle(container):
    code = """import json,os,psycopg
with psycopg.connect(os.environ['DISCORD_RECORDS_DSN'],connect_timeout=5) as conn:
    n=conn.execute("SELECT count(*) FROM discord_records.events WHERE status='running' AND created_at>now()-interval '15 minutes'").fetchone()[0]
    n+=conn.execute("SELECT count(*) FROM discord_records.sessions WHERE document->'generation'->>'status' IN ('planning','preparing_data','validating')").fetchone()[0]
print(json.dumps({'active':n}))"""
    if json.loads(command('docker', 'exec', container, 'python', '-c', code))['active']:
        raise DeploymentError('A bot request is active; retry after it finishes')


def runtime_status(container):
    current = inspect_container(container)
    labels = current['Config']['Labels'] or {}
    result = dict(container=container, running=current['State']['Running'], image_id=current['Image'],
                revision=labels.get('org.opencontainers.image.revision'),
                source_ref=labels.get('io.da-agent.source-ref'))
    environment = dict(value.split('=', 1) for value in current['Config'].get('Env', []) if '=' in value)
    result['llm_provider'] = environment.get('DISCORD_LLM_PROVIDER', 'gemma')
    if result['running'] and result['revision']:
        verified = json.loads(command('docker', 'exec', container, 'python', 'scripts/check_discord_release.py'))
        if verified['revision'] != result['revision'] or verified['source_ref'] != result['source_ref']:
            raise DeploymentError('Image labels and running release disagree')
        result['files_verified'] = verified['files_verified']
    return result


def wait_ready(container, revision, started):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        current = inspect_container(container)
        if current['RestartCount'] or not current['State']['Running']:
            raise DeploymentError('The new bot failed to stay running')
        logs = command('docker', 'logs', '--since', started, container)
        if 'Discord ready:' in logs:
            result = json.loads(command('docker', 'exec', container, 'python', 'scripts/check_discord_release.py'))
            if result['revision'] != revision or result['source_ref'] != 'refs/heads/main':
                raise DeploymentError('Running revision does not match the selected main commit')
            return result
        time.sleep(2)
    raise DeploymentError('Discord readiness timed out')


def wait_restored(container, image_id, started):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        current = inspect_container(container)
        if current['Image'] == image_id and current['State']['Running'] and not current['RestartCount']:
            if 'Discord ready:' in command('docker', 'logs', '--since', started, container):
                return
        time.sleep(2)
    raise DeploymentError('Previous bot did not recover its connection')


def codex_spec(spec, home, model=None, timeout=180):
    """Explicit release-time provider migration; retain unrelated runtime state."""
    home = Path(home).resolve()
    if not home.is_dir() or any(c in str(home) for c in (',', '\n', '\r')):
        raise DeploymentError('--codex-home must be an existing dedicated Codex directory without commas')
    if model and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}', model):
        raise DeploymentError('Invalid --codex-model')
    if not 1 <= timeout <= 600:
        raise DeploymentError('--codex-timeout must be between 1 and 600')
    bot = spec['services']['bot']
    settings = {'DISCORD_LLM_PROVIDER': 'codex_cli', 'DISCORD_CODEX_HOME': '/run/codex',
                'DISCORD_CODEX_BIN': '/usr/local/bin/codex',
                'DISCORD_CODEX_MODEL': model or '', 'DISCORD_CODEX_TIMEOUT_SECONDS': str(timeout)}
    bot['environment'] = [v for v in bot['environment'] if v.split('=', 1)[0] not in settings]
    bot['environment'] += [f'{k}={v}' for k, v in settings.items()]
    bot['volumes'] = [v for v in bot['volumes'] if v['target'] != '/run/codex']
    bot['volumes'].append(dict(type='bind', source=str(home).replace('$', '$$'),
                               target='/run/codex', read_only=False))
    return spec


def deploy(root, revision, container, directory, *, codex_home=None, codex_model=None, codex_timeout=180):
    current = inspect_container(container)
    project = current['Config']['Labels']['com.docker.compose.project']
    ensure_idle(container)
    image = 'da-agent-discord-bot:git-' + revision
    with tempfile.TemporaryDirectory(prefix='source-', dir=directory) as work:
        context = Path(work)
        archive = subprocess.check_output(['git', 'archive', '--format=tar', revision], cwd=root)
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            tar.extractall(context, filter='data')
        print('Building exact main commit ' + revision, flush=True)
        command('docker', 'build', '-f', 'Dockerfile.discord', '--build-arg', 'GIT_SHA=' + revision,
                '--build-arg', 'SOURCE_REF=refs/heads/main', '-t', image, str(context), cwd=context)
        manifest = json.loads(command('docker', 'run', '--rm', '--entrypoint', 'cat', image, '/app/release.json'))
        if manifest['revision'] != revision or manifest['source_ref'] != 'refs/heads/main':
            raise DeploymentError('Candidate image revision mismatch')
        for name, digest in manifest['files'].items():
            path = (context / name).resolve()
            if not path.is_relative_to(context.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise DeploymentError('Candidate source differs from the selected Git commit')
        command('docker', 'run', '--rm', '--entrypoint', 'python', image, 'scripts/check_discord_release.py')
    candidate = compose_spec(current, image)
    if codex_home is not None:
        candidate = codex_spec(candidate, codex_home, codex_model, codex_timeout)
        probe = """import sys
from da_agent.codex_provider import CodexCliProvider
p=CodexCliProvider(executable='/usr/local/bin/codex',home='/run/codex')
sys.exit(0 if p.status().get('connected') else 1)
"""
        command('docker', 'run', '--rm', '--mount',
                f'type=bind,source={Path(codex_home).resolve()},target=/run/codex',
                '--entrypoint', 'python', image, '-c', probe)
    ensure_idle(container)
    if inspect_container(container)['Image'] != current['Image']:
        raise DeploymentError('Another operator changed the bot during build; retry from its current configuration')
    previous_image = 'da-agent-discord-bot:rollback-' + current['Image'].split(':')[-1][:16]
    command('docker', 'image', 'tag', current['Image'], previous_image)
    release_dir = directory / revision
    release_dir.mkdir(exist_ok=True)
    candidate_path, previous_path = release_dir / 'compose.json', release_dir / 'previous-compose.json'
    write_json(candidate_path, candidate)
    write_json(previous_path, compose_spec(current, previous_image))
    started = datetime.now(timezone.utc).isoformat()
    journal = dict(revision=revision, source_ref='refs/heads/main', status='applying', started_at=started,
                   container=container, previous_image_id=current['Image'], image=image)
    write_json(release_dir / 'deployment.json', journal)
    try:
        command('docker', 'compose', '-p', project, '-f', str(candidate_path), 'up', '-d', '--no-deps', '--no-build', 'bot')
        verified = wait_ready(container, revision, started)
        journal.update(status='verified', verified_at=datetime.now(timezone.utc).isoformat(),
                       image_id=inspect_container(container)['Image'], files_verified=verified['files_verified'])
        write_json(release_dir / 'deployment.json', journal)
        write_json(directory / 'current.json', journal)
        print(json.dumps({k: journal[k] for k in ('revision', 'status', 'image_id', 'files_verified')}), flush=True)
    except Exception:
        journal['status'] = 'verification_failed'
        write_json(release_dir / 'deployment.json', journal)
        try:
            restored_at = datetime.now(timezone.utc).isoformat()
            command('docker', 'compose', '-p', project, '-f', str(previous_path), 'up', '-d', '--no-deps', '--no-build', 'bot')
            wait_restored(container, current['Image'], restored_at)
            journal['status'] = 'rolled_back'
        except Exception:
            journal['status'] = 'rollback_failed'
        write_json(release_dir / 'deployment.json', journal)
        raise DeploymentError('Deployment failed verification; recovery status: ' + journal['status']) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', help='Full SHA already in main; default is current origin/main')
    parser.add_argument('--container', default='da-agent-discord-bot-1')
    parser.add_argument('--apply', action='store_true', help='Apply the verified main release')
    parser.add_argument('--status', action='store_true', help='Read the running revision without deploying')
    parser.add_argument('--provider', choices=['codex_cli'], help='Explicitly switch to subscription CLI during this release')
    parser.add_argument('--codex-home', help='Dedicated, authenticated host directory mounted writable for token refresh')
    parser.add_argument('--codex-model', help='Optional selected Codex model; otherwise CLI default')
    parser.add_argument('--codex-timeout', type=int, default=180)
    args = parser.parse_args()
    if bool(args.provider) != bool(args.codex_home):
        raise DeploymentError('--provider codex_cli and --codex-home must be supplied together')
    if (args.codex_model or args.codex_timeout != 180) and not args.provider:
        raise DeploymentError('Codex overrides require --provider codex_cli')
    if args.status:
        print(json.dumps(runtime_status(args.container)))
        return
    root = Path(__file__).resolve().parents[1]
    repository, revision = main_revision(root, args.commit)
    require_ci(repository, revision)
    if not args.apply:
        if args.provider:
            codex_spec({'services': {'bot': {'environment': [], 'volumes': []}}},
                       args.codex_home, args.codex_model, args.codex_timeout)
        print(json.dumps(dict(plan_only=True, revision=revision, source_ref='refs/heads/main',
                              container=args.container, provider=args.provider or 'preserve_current')))
        return
    directory = root / '.local' / 'releases'
    directory.mkdir(parents=True, exist_ok=True)
    with release_lock(directory):
        deploy(root, revision, args.container, directory, codex_home=args.codex_home,
               codex_model=args.codex_model, codex_timeout=args.codex_timeout)


if __name__ == '__main__':
    try:
        main()
    except DeploymentError as exc:
        print(str(exc), flush=True)
        raise SystemExit(1) from None
