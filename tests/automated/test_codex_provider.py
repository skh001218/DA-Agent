import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from da_agent import codex_provider as cli
from da_agent.discord_bot import DiscordSettings
from da_agent.discord_provider import configured_discord_provider
from da_agent.errors import DomainError


def events(body='{"ok":true}', terminal=True):
    rows = [{'type': 'thread.started', 'thread_id': 'private-thread'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': body}}]
    if terminal:
        rows.append({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'output_tokens': 3,
                                                       'private': 'must-not-escape'}})
    return '\n'.join(json.dumps(r) for r in rows)


@pytest.fixture
def transport(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.CodexCliProvider, '_binary', lambda self: 'trusted-codex')
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[-2:] == ['login', 'status']:
            return 0, '', 'Logged in using ChatGPT'
        return 0, events(), 'private stderr'
    monkeypatch.setattr(cli, 'run_cli', run)
    return calls


def test_subscription_transport_keeps_prompt_off_commandline_and_isolates_environment(transport, tmp_path, monkeypatch):
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'GEMINI_API_KEY',
                 'DISCORD_BOT_TOKEN', 'DISCORD_RECORDS_DSN', 'OPENAI_IDENTITY_TOKEN_FILE',
                 'OPENAI_FEDERATION_RULE_ID', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE'):
        monkeypatch.setenv(name, 'private')
    provider = cli.CodexCliProvider(home=tmp_path, model='gpt-6.1-sol')
    charged = Mock()
    result = provider.review([{'role': 'user', 'content': 'learner input $(secret) & | "'}], before_send=charged)
    args, params = transport[-1]
    assert result == {'state': 'completed', 'text': '{"ok": true}', 'model': 'gpt-6.1-sol',
                      'usage': {'input_tokens': 12, 'output_tokens': 3}}
    assert args[-1] == '-' and 'learner input' not in str(args)
    assert 'learner input' in params['prompt'] and args[args.index('--sandbox')+1] == 'read-only'
    for setting in ('approval_policy="never"', 'forced_login_method="chatgpt"', 'features.shell_tool=false',
                    'features.apps=false', 'features.plugins=false', 'features.multi_agent=false', 'web_search="disabled"'):
        assert setting in args
    assert '--ignore-user-config' in args and '--ephemeral' in args
    assert params['env']['CODEX_HOME'] == str(tmp_path.resolve())
    assert 'private' not in str(params['env']) and not Path(params['cwd']).exists()
    charged.assert_called_once_with()
    assert provider.status()['inference_verified']


def test_schema_stays_in_temporary_workspace_and_public_dictionary_is_not_response_schema(transport, monkeypatch):
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
              'required': ['ok'], 'additionalProperties': False}
    observed = []
    original = cli.run_cli
    def run(args, **kwargs):
        if '--output-schema' in args:
            path = Path(args[args.index('--output-schema')+1])
            observed.append((path, json.loads(path.read_text(encoding='utf-8'))))
        return original(args, **kwargs)
    monkeypatch.setattr(cli, 'run_cli', run)
    p = cli.CodexCliProvider()
    assert p.review([{'role': 'user', 'content': json.dumps({'schema': schema})}])['state'] == 'completed'
    assert observed[0][1] == schema and not observed[0][0].exists()
    p.review([{'role': 'user', 'content': json.dumps({'schema': {'users': {'id': 'integer'}}})}])
    assert '--output-schema' not in transport[-1][0]


@pytest.mark.parametrize('body', ['[]', 'prefix {"ok":true}', '{bad}', '{"ok":NaN}', '\x60\x60\x60json\n{"ok":true}\n\x60\x60\x60'])
def test_invalid_final_json_fails_closed(body):
    result = cli.CodexCliProvider()._response(0, events(body), 'secret')
    assert result['reason'] == 'provider_invalid_json' and result['text'] == ''
    assert 'secret' not in str(result)


def test_terminal_event_is_required_and_error_after_partial_text_wins():
    p = cli.CodexCliProvider()
    assert p._response(0, events(terminal=False), '')['reason'] == 'response_incomplete'
    failed = events(terminal=False) + '\n' + json.dumps(
        {'type': 'turn.failed', 'error': {'message': 'private account reached usage limit'}})
    result = p._response(0, failed, '')
    assert result['reason'] == 'usage_limit_exceeded'
    assert 'private account' not in str(result) and not p.verified
    assert p._response(1, events(), 'model_not_found PRIVATE')['reason'] == 'model_unavailable'
    assert p._response(0, events()+'\n'+json.dumps({'type':'turn.interrupted'}), '')['reason'] == 'response_incomplete'


def test_recipe_contract_is_preserved_in_prompt_without_incompatible_strict_schema(transport):
    from da_agent.adaptive_tasks import recipe_response_schema
    schema = recipe_response_schema()
    cli.CodexCliProvider().review([{'role':'user','content':json.dumps({'schema':schema})}])
    args, params = transport[-1]
    assert '--output-schema' not in args
    assert json.loads(json.loads(params['prompt'])['messages'][0]['content'])['schema'] == schema


@pytest.mark.parametrize('kind', ['command_execution', 'mcp_tool_call', 'web_search', 'file_change'])
def test_tool_events_are_rejected(kind):
    out = json.dumps({'type': 'item.started', 'item': {'type': kind, 'command': 'private'}}) + '\n' + events()
    result = cli.CodexCliProvider()._response(0, out, '')
    assert result['reason'] == 'codex_unexpected_tool' and 'private' not in str(result)


@pytest.mark.parametrize('status', [(1, '', 'Not logged in'), (0, 'Logged in using an API key', '')])
def test_login_or_api_auth_never_calls_model(status, transport, monkeypatch):
    monkeypatch.setattr(cli, 'run_cli', lambda *a, **k: status)
    charged = Mock()
    p = cli.CodexCliProvider()
    assert p.review([], before_send=charged)['reason'] == 'codex_login_required'
    charged.assert_not_called()
    assert not p.status()['connected']


@pytest.mark.parametrize('reason', ['usage_limit', 'cancelled', 'planning_limit'])
def test_application_reservation_can_block_before_exec(reason, transport):
    def rejected():
        raise DomainError(reason, 'private')
    result = cli.CodexCliProvider().review([], before_send=rejected)
    assert result['reason'] == reason and len(transport) == 1 and 'private' not in str(result)


def test_no_research_fallback_or_model_switch_and_preflight_input_limit(transport, monkeypatch):
    p = cli.CodexCliProvider()
    assert p.research([])['reason'] == 'research_unavailable'
    assert p.review([], model='gemma-4-test')['reason'] == 'model_unavailable'
    monkeypatch.setattr(cli, 'MAX_INPUT_BYTES', 10)
    assert p.review([{'role':'user','content':'x'*30}])['reason'] == 'api_input_budget'
    assert not transport


def test_missing_binary_never_reserves_or_executes(monkeypatch):
    monkeypatch.setattr(cli.shutil, 'which', lambda _: None)
    charged = Mock()
    p = cli.CodexCliProvider()
    assert p.review([], before_send=charged)['reason'] == 'codex_cli_unavailable'
    charged.assert_not_called()


def test_real_subprocess_stdin_and_failure_output_are_captured_without_shell(tmp_path):
    args = [sys.executable, '-c', 'import sys; print(sys.stdin.read()); print("private",file=sys.stderr)']
    code, out, err = cli.run_cli(args, env=cli.child_environment(), cwd=tmp_path, prompt='한글 $(do-not-execute)')
    assert code == 0 and '$(do-not-execute)' in out and 'private' in err


def test_real_subprocess_timeout_terminates_process(tmp_path):
    with pytest.raises(cli.CliFailure) as caught:
        cli.run_cli([sys.executable, '-c', 'import time; time.sleep(10)'],
                    env=cli.child_environment(), cwd=tmp_path, timeout=0.15)
    assert caught.value.reason == 'codex_timeout'


def test_real_subprocess_output_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'MAX_OUTPUT_BYTES', 100)
    with pytest.raises(cli.CliFailure) as caught:
        cli.run_cli([sys.executable, '-c', 'print("x"*10000)'], env=cli.child_environment(), cwd=tmp_path)
    assert caught.value.reason == 'codex_output_limit'


def settings_env():
    return {'DISCORD_BOT_TOKEN': 'not-real', 'DISCORD_GUILD_IDS': '10',
            'DISCORD_RECORDS_DSN': 'postgresql://u:p@localhost/discord_records',
            'DISCORD_ADMIN_DSN': 'postgresql://a:p@localhost/discord_training',
            'DISCORD_LEARNER_DSN': 'postgresql://l:p@localhost/discord_training',
            'DISCORD_LLM_PROVIDER': 'codex_cli', 'DISCORD_MODEL': 'gemma-legacy'}


def test_codex_settings_do_not_require_gemma_key_or_reuse_web_model():
    settings = DiscordSettings.from_env(settings_env())
    assert settings.gemini_key_file is None and settings.llm_model == 'codex-default'
    p = configured_discord_provider(settings)
    assert isinstance(p, cli.CodexCliProvider) and p.selected_model is None


@pytest.mark.parametrize('values', [
    {'DISCORD_LLM_PROVIDER':'unknown'}, {'DISCORD_CODEX_MODEL':'bad / model'},
    {'DISCORD_CODEX_TIMEOUT_SECONDS':'0'}, {'DISCORD_CODEX_TIMEOUT_SECONDS':'601'},
    {'DISCORD_CODEX_TIMEOUT_SECONDS':'invalid'},
])
def test_bad_codex_settings_are_rejected(values):
    with pytest.raises(ValueError):
        DiscordSettings.from_env(dict(settings_env(), **values))


def test_codex_user_errors_do_not_give_google_api_instructions():
    from da_agent.discord_generation import failure_message
    from da_agent.reviews import normalize_ai
    for reason, message in cli.CLI_ERRORS.items():
        assert failure_message(reason) == message
        assert normalize_ai({'state':'error','reason':reason})['error']['message'] == message
