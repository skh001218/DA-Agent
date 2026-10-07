from copy import deepcopy

import pytest

from da_agent.discord_presentation import task_intro, reference_info
from da_agent.discord_task_brief import period_lines, background_preview, public_sections
from da_agent.discord_transport import safe_chunks
from da_agent.task_quality import public_description
from da_agent.adaptive_tasks import Recipe
from test_adaptive_tasks import bot_recipe
from test_discord_basic_template import sample_document
from test_discord_generation import generated_service


def period_task(description, timezone='Asia/Seoul', cutoff=None):
    return {'period':{'description':description,'observation_end':cutoff}, 'timezone':timezone}


def test_utc_two_windows_become_distinct_korean_calendar_ranges():
    task = period_task('UTC 기준 window_a: 2026-08-01T00:00:00Z~2026-08-07T23:59:59Z, '
        'window_b: 2026-08-08T00:00:00Z~2026-08-14T23:59:59Z.', cutoff='2026-08-14T23:45:41+00:00')
    before = deepcopy(task)
    rendered = '\n'.join(period_lines(task))
    assert '한국 시간 · 2026년' in rendered
    assert '기간 A: 8/1 09:00 ~ 8/8 08:59:59' in rendered
    assert '기간 B: 8/8 09:00 ~ 8/15 08:59:59' in rendered
    assert '8/15 08:45:41 미만' in rendered
    assert 'T00:' not in rendered and '+00:00' not in rendered and 'UTC' not in rendered
    assert task == before


@pytest.mark.parametrize('description,zone,expected', [
    ('2026-09-01 하루','Asia/Seoul','9/1 하루'),
    ('UTC 기준 2026-09-01 하루','Asia/Seoul','9/1 09:00 ~ 9/2 09:00 미만'),
    ('2026-09-01T00:00:00+09:00~2026-09-02T00:00:00+09:00 미만','UTC','9/1 00:00 ~ 9/2 00:00 미만'),
    ('UTC 기준 2026-09-01 00:00~2026-09-02 00:00 미만','Asia/Seoul','9/1 09:00 ~ 9/2 09:00 미만'),
    ('2026-12-31T00:00:00Z~2027-01-01T00:00:00Z','UTC','2026/12/31 09:00 ~ 2027/1/1 09:00'),
    ('2026-09-01~2026-09-08 (종료일 제외)','Asia/Seoul','9/1 ~ 9/8 미만'),
    ('2026-09-01T00:00:00.123Z~2026-09-02T00:00:00.456Z','UTC','9/1 09:00:00.123 ~ 9/2 09:00:00.456'),
])
def test_public_boundaries_offsets_precision_and_year_are_preserved(description, zone, expected):
    assert expected in '\n'.join(period_lines(period_task(description, zone)))


@pytest.mark.parametrize('description,zone', [
    ('요청한 관측 범위','Asia/Seoul'), ('2026-99-01~2026-99-02','Asia/Seoul'),
    ('2026-09-02~2026-09-01','UTC'), ('2026-09-01~2026-09-02','unknown/timezone'),
    ('두 기간 후보 2026-09-01 또는 2026-09-02','UTC'),
    ('2026-09-01~2026-09-02','UTC'),
])
def test_unparseable_periods_are_not_replaced_with_inferred_data_dates(description, zone):
    task = period_task(description, zone)
    task['period'].update(start='2001-01-01',end='2001-02-02')
    rendered = '\n'.join(period_lines(task))
    assert '2001' not in rendered
    assert '원문' in rendered and '한국 시간' not in rendered


def test_legacy_public_description_recovers_questions_but_never_coaching_paths():
    doc = sample_document()
    task = doc['task']
    del task['intro_sections']
    recipe = Recipe.model_validate(bot_recipe())
    task['objective'] = public_description(recipe)
    task['valid_paths'] = ['공개 도움을 요청할 때만 제공하는 비필수 풀이 순서']
    before = deepcopy(task)
    brief = task_intro(doc)
    original = reference_info(task, 'task_details')
    background = background_preview(task)
    assert len(background) <= 180
    assert task['objective'] not in brief
    assert original.count(task['objective']) == 1
    assert public_sections(task)['questions'] == [r.question for r in recipe.business_case.requirements]
    for index, requirement in enumerate(recipe.business_case.requirements, 1):
        assert f'{index}. {requirement.question}' in brief
    assert task['valid_paths'][0] not in brief and task['valid_paths'][0] not in original
    assert task == before
    assert len(safe_chunks(brief)) == 1


@pytest.mark.parametrize('state', ['analysis','stopped','completed'])
def test_original_reference_is_available_without_model_or_coaching_history(generated_service, state):
    service, session, stage = generated_service
    doc = service.generate('owner',session['session_id'])
    with service.store.edit('owner',session['session_id']) as (stored,_):
        stored['state'] = state
    calls = service.store.calls
    before = service.store.get('owner',session['session_id'])
    response = service.handle('owner',session['session_id'],'details-' + state,'help',payload={'help_type':'task_details'})
    assert doc['task']['objective'] in response['messages'][0]
    assert service.store.calls == calls
    after = service.store.get('owner',session['session_id'])
    assert after['task'] == before['task']
    assert after['help_history'] == before['help_history']
    assert after['state'] == state
