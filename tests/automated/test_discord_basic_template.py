"""Public task conditions survive the basic introduction and Discord transport."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from da_agent.adaptive_tasks import Recipe
from da_agent.discord_generation import task_from_recipe
from da_agent.discord_presentation import task_intro, reference_info
from da_agent.discord_transport import DiscordTransport, safe_chunks
from da_agent.evaluation_registry import task_key
from test_adaptive_tasks import bot_recipe
from test_discord_generation import generated_service, public


def sample_document():
    recipe = Recipe.model_validate(bot_recipe())
    task = task_from_recipe(recipe, public(), {'version':'discord-synthetic-v1', 'sources':[]}, 'independent')
    return {'task':task, 'practice':'analysis'}


@pytest.mark.parametrize('difficulty,label', [('beginner','초급'), ('intermediate','중급'), ('advanced','고급')])
def test_basic_intro_preserves_public_questions_and_conditions(difficulty, label):
    doc = sample_document()
    task = doc['task']
    task['difficulty'] = difficulty
    before = deepcopy(doc)
    profile = task_key(task)
    rendered = task_intro(doc)
    details = reference_info(task, 'task_details')
    recipe = Recipe.model_validate(bot_recipe())
    assert f'난이도: {label}' in rendered
    for index, requirement in enumerate(recipe.business_case.requirements, 1):
        assert f'{index}. {requirement.question}' in rendered
    for value in [recipe.description, recipe.business_case.background, recipe.business_case.observed_problem,
                  recipe.business_case.decision, *recipe.business_case.agent_assumptions,
                  *task['accepted_limits'], task['period']['description'], task['timezone'],
                  task['quality_information']['collection'], task['quality_information']['verification_scope']]:
        assert value in details
    assert '9/1 하루' in rendered
    assert '9/2 00:00 미만' in rendered
    for name, entry in task['dictionary'].items():
        assert name + ' — ' + entry['unit'] in rendered
    assert '가상 분석 문제' in rendered and '실제 사례 검색은 수행하지 않았습니다' in rendered
    assert 'regular_activity_rows' not in rendered and '1000' not in rendered
    assert doc == before and task_key(task) == profile


def test_explicit_judgment_rules_survive_structured_projection():
    recipe = Recipe.model_validate(bot_recipe())
    from da_agent.task_quality import JudgmentContract
    recipe.business_case.requirements[0].judgment = JudgmentContract(
        method='conditional_comparison', control_columns=['accounts.platform'],
        decision_rule='동일 플랫폼에서 관측 차이를 비교하고 근거가 부족하면 판단을 보류하세요.',
        accepted_limit='하루 집계만으로 부정행위나 원인을 확정할 수 없음을 인정합니다.')
    task = task_from_recipe(recipe, public(), {}, 'independent')
    rendered = task_intro({'task':task})
    judgment = recipe.business_case.requirements[0].judgment
    details = reference_info(task, 'task_details')
    assert '문제 원문' in rendered and judgment.method not in rendered
    assert judgment.control_columns[0] in details
    assert judgment.decision_rule in details and judgment.accepted_limit in details


def test_older_generated_tasks_keep_full_request_without_turning_hints_into_steps():
    doc = sample_document()
    task = doc['task']
    del task['intro_sections']
    task['objective'] = '예전 공개 업무 요청과 필요한 조건을 모두 보존하세요.'
    task['valid_paths'] = ['공개 도움을 요청할 때만 제공하는 풀이 힌트']
    task['source_case'] = {'sources':[{'title':'저장된 사례', 'url':'https://example.com/case'}]}
    task['period']['observation_end'] = None
    before = deepcopy(doc)
    rendered = task_intro(doc)
    assert rendered.count(task['objective']) == 1
    assert task['valid_paths'][0] not in rendered
    assert '자료 관측 종료:' not in rendered
    assert '저장된 사례 https://example.com/case' in rendered
    assert doc == before


def test_long_intro_transport_preserves_all_content_and_blocks_mentions():
    doc = sample_document()
    doc['task']['intro_sections']['questions'] = [f'{index}번 @everyone **업무 요청** ' * 22 for index in range(6)]
    original = task_intro(doc)
    chunks = safe_chunks(original)
    assert len(chunks) > 1 and all(len(chunk) <= 1900 for chunk in chunks)
    assert all('@everyone' not in chunk for chunk in chunks)
    assert '/submit' in chunks[-1]
    for question in doc['task']['intro_sections']['questions']:
        assert safe_chunks(question, limit=10000)[0] in ''.join(chunks)
    sent = []

    async def send(channel, text):
        sent.append(text)
        return NS(id=len(sent))

    transport = DiscordTransport(NS(), NS(send=send), ['guild'])
    asyncio.run(transport._emit(NS(), [original], doc))
    assert sent == chunks


def test_ready_generation_and_resume_share_template_without_new_calls(generated_service):
    service, session, stage = generated_service
    doc = service.generate('owner', session['session_id'])
    before = deepcopy(doc)
    calls = service.store.calls
    response = service.resume('owner', 'guild', session['session_id'])
    assert response['messages'][0] == task_intro(doc)
    assert '1. ' + doc['task']['intro_sections']['questions'][0] in response['messages'][0]
    assert service.store.calls == calls
    assert service.store.get('owner', session['session_id']) == before
    stage.assert_called_once()
