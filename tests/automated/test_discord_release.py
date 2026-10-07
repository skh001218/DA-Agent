"""Release gates protect commit provenance, source integrity and stored data."""
import hashlib
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile

import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load('deploy_discord')
checker = load('check_discord_release')


def test_cli_release_configuration_preserves_data_and_updates_only_selected_settings(tmp_path):
    spec = {'services': {'bot': {
        'environment': ['DISCORD_LLM_PROVIDER=gemma', 'DISCORD_CODEX_MODEL=old',
                        'DISCORD_RECORDS_DSN=private', 'OTHER=value'],
        'volumes': [{'type':'volume','source':'records','target':'/app/.local/discord-responses'},
                    {'type':'bind','source':'old-home','target':'/run/codex'}]}},
        'networks': {'existing': {'external': True}}}
    value = release.codex_spec(spec, tmp_path, 'gpt-6.1-sol', 90)
    bot = value['services']['bot']
    assert bot['environment'].count('DISCORD_LLM_PROVIDER=codex_cli') == 1
    assert 'DISCORD_CODEX_MODEL=old' not in bot['environment']
    assert 'DISCORD_RECORDS_DSN=private' in bot['environment'] and 'OTHER=value' in bot['environment']
    assert bot['volumes'][0]['source'] == 'records'
    assert bot['volumes'][1]['source'] == str(tmp_path.resolve()) and not bot['volumes'][1]['read_only']
    assert value['networks'] == {'existing': {'external': True}}


@pytest.mark.parametrize('model,timeout', [('bad/model',180),(None,0),(None,601)])
def test_cli_release_rejects_invalid_config(tmp_path, model, timeout):
    with pytest.raises(release.DeploymentError):
        release.codex_spec({'services':{'bot':{'environment':[],'volumes':[]}}},tmp_path,model,timeout)


def test_cli_release_rejects_missing_auth_directory(tmp_path):
    with pytest.raises(release.DeploymentError):
        release.codex_spec({'services':{'bot':{'environment':[],'volumes':[]}}},tmp_path/'missing')


def test_cli_auth_preflight_failure_keeps_running_bot_untouched(tmp_path, monkeypatch):
    revision = 'b' * 40
    current = dict(Image='sha256:existing', Config={'Labels': {'com.docker.compose.project': 'fixture'}})
    monkeypatch.setattr(release, 'inspect_container', lambda _: current)
    monkeypatch.setattr(release, 'ensure_idle', lambda _: None)
    monkeypatch.setattr(release, 'compose_spec', lambda _, image: {'services':{'bot':{
        'image':image,'environment':[],'volumes':[]}}})
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode='w'): pass
    monkeypatch.setattr(release.subprocess, 'check_output', lambda *a, **k: stream.getvalue())
    calls = []
    def command(*args, **kwargs):
        calls.append(args)
        if '/app/release.json' in args:
            return json.dumps(dict(revision=revision, source_ref='refs/heads/main', files={}))
        if any('p=CodexCliProvider' in str(value) for value in args):
            raise release.DeploymentError('authentication failed')
        return '{}'
    monkeypatch.setattr(release, 'command', command)
    with pytest.raises(release.DeploymentError, match='authentication failed'):
        release.deploy(tmp_path, revision, 'bot', tmp_path, codex_home=tmp_path)
    assert not any(c[:2] == ('docker','compose') for c in calls)
    assert not (tmp_path / revision).exists()


def checks():
    return [dict(id=i, name=name, status='completed', conclusion='success', app={'slug': 'github-actions'})
            for i, name in enumerate(sorted(release.REQUIRED_CHECKS))]


@pytest.mark.parametrize('limit', [0, 300])
def test_daily_limit_release_changes_only_the_selected_setting(limit):
    spec = {'services': {'bot': {'environment': ['DISCORD_DAILY_CALL_LIMIT=30',
        'DISCORD_RECORDS_DSN=private', 'DISCORD_LLM_PROVIDER=codex_cli'], 'volumes': ['existing']}},
        'networks': {'existing': {'external': True}}}
    result = release.daily_limit_spec(spec, limit)
    assert result['services']['bot']['environment'] == ['DISCORD_RECORDS_DSN=private',
        'DISCORD_LLM_PROVIDER=codex_cli', f'DISCORD_DAILY_CALL_LIMIT={limit}']
    assert result['services']['bot']['volumes'] == ['existing']
    assert result['networks'] == {'existing': {'external': True}}


@pytest.mark.parametrize('limit', [-1, '0', True])
def test_daily_limit_release_rejects_invalid_values_before_runtime_access(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(release, 'inspect_container', lambda _: pytest.fail('runtime must not be accessed'))
    with pytest.raises(release.DeploymentError, match='non-negative'):
        release.deploy(tmp_path, 'a' * 40, 'bot', tmp_path, daily_call_limit=limit)


def test_runtime_status_reports_unlimited_without_disclosing_other_environment(monkeypatch):
    monkeypatch.setattr(release, 'inspect_container', lambda _: {'Config': {
        'Labels': {}, 'Env': ['DISCORD_DAILY_CALL_LIMIT=0', 'DISCORD_BOT_TOKEN=private']},
        'State': {'Running': True}, 'Image': 'fixture'})
    status = release.runtime_status('bot')
    assert status['daily_call_limit'] == 0 and 'private' not in json.dumps(status)


@pytest.mark.parametrize('applied_limit', [0, 30])
def test_daily_limit_deployment_verifies_setting_and_restores_previous_on_mismatch(tmp_path, monkeypatch, applied_limit):
    revision = 'c' * 40
    current = dict(Image='sha256:old', Config={'Labels': {'com.docker.compose.project': 'fixture'}})
    monkeypatch.setattr(release, 'inspect_container', lambda _: current)
    monkeypatch.setattr(release, 'ensure_idle', lambda _: None)
    monkeypatch.setattr(release, 'compose_spec', lambda _, image: {'services': {'bot': {
        'image': image, 'environment': ['DISCORD_DAILY_CALL_LIMIT=30', 'OTHER=preserved']}}})
    monkeypatch.setattr(release, 'wait_ready', lambda *_: {'files_verified': 82})
    monkeypatch.setattr(release, 'runtime_status', lambda _: {'daily_call_limit': applied_limit})
    restored = []
    monkeypatch.setattr(release, 'wait_restored', lambda *_: restored.append(True))
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode='w'): pass
    monkeypatch.setattr(release.subprocess, 'check_output', lambda *_, **__: stream.getvalue())
    def fake_command(*args, **kwargs):
        if '/app/release.json' in args:
            return json.dumps(dict(revision=revision, source_ref='refs/heads/main', files={}))
        return '{}'
    monkeypatch.setattr(release, 'command', fake_command)
    if applied_limit == 0:
        release.deploy(tmp_path, revision, 'bot', tmp_path, daily_call_limit=0)
        assert json.loads((tmp_path / 'current.json').read_text())['status'] == 'verified'
        assert not restored
    else:
        with pytest.raises(release.DeploymentError, match='rolled_back'):
            release.deploy(tmp_path, revision, 'bot', tmp_path, daily_call_limit=0)
        assert restored and not (tmp_path / 'current.json').exists()
    candidate = json.loads((tmp_path / revision / 'compose.json').read_text())
    previous = json.loads((tmp_path / revision / 'previous-compose.json').read_text())
    assert candidate['services']['bot']['environment'] == ['OTHER=preserved', 'DISCORD_DAILY_CALL_LIMIT=0']
    assert previous['services']['bot']['environment'] == ['DISCORD_DAILY_CALL_LIMIT=30', 'OTHER=preserved']


def test_ci_requires_all_latest_successful_github_action_checks():
    valid = checks()
    assert release.approved_checks(valid)
    assert not release.approved_checks(valid[:-1])
    assert not release.approved_checks([dict(c, app={'slug': 'other-app'}) for c in valid])
    assert not release.approved_checks(valid + [dict(valid[0], id=100, conclusion='failure')])
    assert not release.approved_checks(valid + [dict(valid[0], id=100, status='in_progress', conclusion=None)])


def test_ci_reuses_existing_git_login_without_prompt_or_output(monkeypatch, capsys):
    def credential(*args, **kwargs):
        assert args[0] == ['git', 'credential', 'fill']
        assert kwargs['env']['GIT_TERMINAL_PROMPT'] == '0'
        assert kwargs['env']['GCM_INTERACTIVE'] == 'Never'
        assert kwargs['capture_output'] is True
        return subprocess.CompletedProcess(args[0], 0, 'password=fixture-secret\n', '')
    monkeypatch.setattr(release.subprocess, 'run', credential)
    assert release.ci_headers()['Authorization'] == 'Bearer fixture-secret'
    assert capsys.readouterr().out == ''


def test_ci_without_git_login_can_read_public_checks_but_fails_closed(monkeypatch):
    monkeypatch.setattr(release.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, '', 'unavailable'))
    assert 'Authorization' not in release.ci_headers()
    monkeypatch.setattr(release, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(OSError('API unavailable')))
    with pytest.raises(release.DeploymentError, match='Could not verify GitHub CI'):
        release.require_ci('skh001218/DA-Agent', 'a' * 40)


def test_main_gate_rejects_unmerged_feature_commit(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path).decode().strip()
    git('init', '-b', 'main')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Release fixture')
    git('remote', 'add', 'origin', 'https://github.com/skh001218/DA-Agent.git')
    (tmp_path / 'source').write_text('main')
    git('add', 'source')
    git('commit', '-m', 'main fixture')
    main = git('rev-parse', 'HEAD')
    git('update-ref', 'refs/remotes/origin/main', main)
    git('switch', '-c', 'codex/feature')
    (tmp_path / 'source').write_text('unmerged')
    git('commit', '-am', 'feature fixture')
    feature = git('rev-parse', 'HEAD')
    original = release.command
    def offline(*args, **kwargs):
        if args[:3] == ('git', 'fetch', 'origin'): return ''
        return original(*args, **kwargs)
    monkeypatch.setattr(release, 'command', offline)
    assert release.main_revision(tmp_path, main) == ('skh001218/DA-Agent', main)
    with pytest.raises(release.DeploymentError, match='Feature-branch'):
        release.main_revision(tmp_path, feature)
    with pytest.raises(release.DeploymentError, match='40-character'):
        release.main_revision(tmp_path, 'main')


@pytest.mark.parametrize('change', ['edit', 'extra', 'missing', 'traversal'])
def test_running_source_manifest_rejects_changes(tmp_path, change):
    (tmp_path / 'src').mkdir()
    source = tmp_path / 'src' / 'app.py'
    source.write_text('original')
    manifest = dict(revision='a' * 40, source_ref='refs/heads/main',
                    files={'src/app.py': hashlib.sha256(source.read_bytes()).hexdigest()})
    if change == 'edit': source.write_text('changed')
    elif change == 'extra': (tmp_path / 'src' / 'untracked.py').write_text('not from commit')
    elif change == 'missing': source.unlink()
    else: manifest['files'] = {'../outside': 'wrong'}
    (tmp_path / 'release.json').write_text(json.dumps(manifest))
    with pytest.raises((RuntimeError, FileNotFoundError)):
        checker.verify(tmp_path)


def test_valid_source_manifest_ignores_only_python_cache(tmp_path):
    (tmp_path / 'src' / '__pycache__').mkdir(parents=True)
    (tmp_path / 'src' / '__pycache__' / 'app.pyc').write_bytes(b'cache')
    source = tmp_path / 'src' / 'app.py'
    source.write_text('original')
    manifest = dict(revision='a' * 40, source_ref='refs/heads/main',
                    files={'src/app.py': hashlib.sha256(source.read_bytes()).hexdigest()})
    (tmp_path / 'release.json').write_text(json.dumps(manifest))
    assert checker.verify(tmp_path) == manifest


def test_compose_preserves_environment_external_volumes_and_network(tmp_path):
    secret = tmp_path / 'token'
    secret.write_text('fixture')
    current = dict(Config=dict(Labels={'com.docker.compose.service': 'bot'},
                               Env=['DISCORD_RECORDS_DSN=fixture-dsn'], WorkingDir='/app'),
        HostConfig=dict(Privileged=False, NetworkMode='project_default', PortBindings={}),
        Mounts=[dict(Type='volume', Name='existing-records', Destination='/app/.local/responses', RW=True),
                dict(Type='bind', Source=str(secret), Destination='/run/secrets/discord_token', RW=False)],
        NetworkSettings={'Networks': {'project_default': {}}})
    spec = release.compose_spec(current, 'reviewed-image')
    assert list(spec['services']) == ['bot']
    assert spec['services']['bot']['environment'] == current['Config']['Env']
    assert spec['volumes']['volume0'] == dict(external=True, name='existing-records')
    assert spec['networks']['network0'] == dict(external=True, name='project_default')
    assert spec['services']['bot']['volumes'][1]['read_only'] is True
    current['Config']['Env'] = ['LITERAL_VALUE=cost$5-${not_a_variable}']
    escaped = release.compose_spec(current, 'reviewed-image')
    assert escaped['services']['bot']['environment'] == ['LITERAL_VALUE=cost$$5-$${not_a_variable}']
    secret.unlink()
    with pytest.raises(release.DeploymentError, match='mount is unavailable'):
        release.compose_spec(current, 'reviewed-image')


def test_docker_desktop_bind_paths_round_trip_after_recreate():
    vm_path = '/run/desktop/mnt/host/c/Users/Administrator/수업자료/token'
    windows_path = 'C:/Users/Administrator/수업자료/token'
    assert release.host_bind_source(vm_path, 'nt') == windows_path
    assert release.host_bind_source(windows_path, 'nt') == windows_path
    assert release.host_bind_source(vm_path, 'posix') == vm_path


def test_concurrent_deployment_is_rejected_and_lock_released(tmp_path):
    with release.release_lock(tmp_path):
        with pytest.raises(release.DeploymentError, match='Another deployment'):
            with release.release_lock(tmp_path): pass
    assert not (tmp_path / 'deploy.lock').exists()


def test_readiness_rejects_restarts_before_certifying_revision(monkeypatch):
    monkeypatch.setattr(release, 'inspect_container', lambda _: {'RestartCount': 1, 'State': {'Running': True}})
    with pytest.raises(release.DeploymentError, match='failed to stay running'):
        release.wait_ready('bot', 'a' * 40, '2026-10-07T00:00:00Z')


@pytest.mark.parametrize('restore_fails', [False, True])
def test_failed_deployment_restores_previous_bot_and_never_certifies_it(tmp_path, monkeypatch, restore_fails):
    revision = 'a' * 40
    current = dict(Image='sha256:old-image', Config={'Labels': {'com.docker.compose.project': 'fixture'}})
    monkeypatch.setattr(release, 'inspect_container', lambda _: current)
    monkeypatch.setattr(release, 'ensure_idle', lambda _: None)
    monkeypatch.setattr(release, 'compose_spec', lambda _, image: {'services': {'bot': {'image': image}}})
    monkeypatch.setattr(release, 'wait_ready', lambda *_: (_ for _ in ()).throw(release.DeploymentError('not ready')))
    def restored(*_):
        if restore_fails: raise release.DeploymentError('restore failed')
    monkeypatch.setattr(release, 'wait_restored', restored)
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode='w'): pass
    monkeypatch.setattr(release.subprocess, 'check_output', lambda *_, **__: stream.getvalue())
    calls = []
    def fake_command(*args, **kwargs):
        calls.append(args)
        if args[:3] == ('docker', 'run', '--rm') and '/app/release.json' in args:
            return json.dumps(dict(revision=revision, source_ref='refs/heads/main', files={}))
        return '{}'
    monkeypatch.setattr(release, 'command', fake_command)
    expected = 'rollback_failed' if restore_fails else 'rolled_back'
    with pytest.raises(release.DeploymentError, match=expected):
        release.deploy(tmp_path, revision, 'bot', tmp_path)
    journal = json.loads((tmp_path / revision / 'deployment.json').read_text())
    assert journal['status'] == expected and not (tmp_path / 'current.json').exists()
    compose_calls = [c for c in calls if c[:2] == ('docker', 'compose')]
    assert len(compose_calls) == 2 and compose_calls[1][5].endswith('previous-compose.json')
