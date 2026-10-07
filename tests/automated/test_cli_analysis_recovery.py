"""Full-recipe recovery through the existing subscription CLI transport."""
from copy import deepcopy
import json

import pytest

from da_agent import codex_provider as cli
from da_agent.discord_generation import retain_alignment_failure
from test_adaptive_tasks import bot_recipe
from test_discord_generation import generated_service


ALIGNED = {'aligned': True, 'issues': [], 'quality_dimensions': {key: 'pass' for key in
    ('business_context', 'evidence_sufficiency', 'difficulty_fit', 'evaluation_alignment')}}
REJECTED = {'aligned': False, 'issues': ['semantic-rejection: 관측되지 않은 효과를 단정하지 마세요.']}


def completed(value):
    return {'state': 'completed', 'text': json.dumps(value, ensure_ascii=False)}


@pytest.mark.parametrize('repair_failure', ['invalid_recipe', 'rate_limit', 'second_rejection'])
def test_cli_failed_alignment_requires_full_recipe_on_manual_retry(generated_service, monkeypatch, repair_failure):
    service, session, stage = generated_service
    sid = session['session_id']
    bad = bot_recipe()
    bad['tables'][0]['groups'][0]['count'] = 2001
    responses = [bot_recipe(), REJECTED]
    responses += ([bad] if repair_failure == 'invalid_recipe' else
                  [None] if repair_failure == 'rate_limit' else [bot_recipe(), REJECTED])
    prompts = []
    monkeypatch.setattr(cli.CodexCliProvider, '_binary', lambda self: 'fixture-codex')

    def run(args, **kwargs):
        if args[-2:] == ['login', 'status']:
            return 0, '', 'Logged in using ChatGPT'
        prompts.append(json.loads(kwargs['prompt']))
        value = responses.pop(0)
        if value is None:
            return 1, '', '429 rate limit'
        events = [{'type':'item.completed', 'item':{'type':'agent_message', 'text':json.dumps(value)}},
                  {'type':'turn.completed', 'usage':{'input_tokens':12, 'output_tokens':5}}]
        return 0, '\n'.join(json.dumps(event) for event in events), ''

    monkeypatch.setattr(cli, 'run_cli', run)
    service.provider = cli.CodexCliProvider()
    failed = service.generate('owner', sid)
    assert failed['generation']['status'] == 'failed'
    stage.assert_not_called()
    job = service.store.generation_job('owner', sid)
    assert 'recipe' not in job and 'alignment' not in job
    assert job['rejected_designs'][-1]['requires_model_repair'] is True
    assert 'semantic-rejection' in str(job['rejected_designs'][-1]['issues'])
    if repair_failure == 'invalid_recipe':
        assert json.loads(job['alignment_candidate']['after']) == bad
    assert 'semantic-rejection' not in json.dumps(failed)
    before = len(prompts)
    responses.extend([bot_recipe(), ALIGNED])
    ready = service.generate('owner', sid, retry=True)
    assert ready['generation']['status'] == 'ready', ready
    assert ready['generation']['completed_steps'] == 3
    assert len(prompts) == before + 2
    repair = prompts[before]['messages']
    assert 'semantic-rejection' in json.dumps(repair, ensure_ascii=False)
    contract = next(json.loads(item['content'])['schema'] for item in repair
                    if item['role'] == 'user' and 'schema' in json.loads(item['content']))
    assert contract['title'] == 'Recipe'
    assert 'recipe_patches' not in service.store.generation_job('owner', sid)
    stage.assert_called_once()
    assert service.store.calls == len(prompts)


def test_semantic_rejection_survives_multiple_invalid_full_recipes(generated_service):
    service, session, stage = generated_service
    sid = session['session_id']
    message = session['generation']['message']
    job = service.store.generation_job('owner', sid)
    job['rejected_designs'] = [{'text':json.dumps(bot_recipe()), 'request_message':message,
                              'issues':REJECTED['issues'], 'requires_model_repair':True}]
    service.store.save_generation_job('owner', sid, job)
    bad = bot_recipe()
    bad['tables'][0]['groups'][0]['count'] = 2001
    service.provider.review.side_effect = [completed(bad)] * 3
    failed = service.generate('owner', sid, retry=True)
    assert failed['generation']['status'] == 'failed'
    stage.assert_not_called()
    job = service.store.generation_job('owner', sid)
    assert all(item['requires_model_repair'] and 'semantic-rejection' in str(item['issues'])
               for item in job['rejected_designs'])
    service.provider.review.side_effect = [completed(bot_recipe()), completed(ALIGNED)]
    ready = service.generate('owner', sid, retry=True)
    assert ready['generation']['status'] == 'ready'
    repair = service.provider.review.call_args_list[3].args[0]
    assert 'semantic-rejection' in str(repair)
    assert service.store.calls == 5


@pytest.mark.parametrize('phase,code,expected', [
    ('adaptive-alignment-repair','plan_invalid',True),
    ('adaptive-alignment','goal_mismatch',True),
    ('adaptive-alignment','provider_invalid_json',False),
    ('adaptive-design','plan_invalid',False),
    ('adaptive-alignment-repair','api_rate_limited',True),
])
def test_legacy_alignment_failure_migration_does_not_discard_unrelated_recipe(phase, code, expected):
    job = {'recipe':bot_recipe(), 'alignment_repair_used':True, 'alignment_errors':[REJECTED['issues']],
           'alignment_candidate':{'phase':'alignment', 'request_message':'older request', 'after':'older draft'}}
    original = deepcopy(job)
    assert retain_alignment_failure(job, 'current request', phase, code, [], revision=1) is expected
    if expected:
        assert json.loads(job['rejected_designs'][-1]['text']) == original['recipe']
        assert 'recipe' not in job
    else:
        assert job == original
