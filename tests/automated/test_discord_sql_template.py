"""SQL public contracts survive template rendering, image ordering and resume."""
import asyncio
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from PIL import Image

from da_agent.adaptive_tasks import Recipe
from da_agent.discord_education import representative_task
from da_agent.discord_generated_sql import make_task as generated_task
from da_agent.discord_generation import task_from_recipe
from da_agent.discord_presentation import task_intro
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_sql_practice import make_task as tutorial_task, TEMPLATE
from da_agent.discord_sql_presentation import sql_intro_messages
from da_agent.discord_tables import dictionary_tables, render_table_png
from da_agent.discord_transport import DiscordTransport, safe_chunks
from test_discord_generated_sql import retention_recipe
from test_discord_generation import public


def sample_document(level='intermediate', generated=True):
    if generated:
        recipe = Recipe.model_validate(retention_recipe(level))
        base = task_from_recipe(recipe, public(), {'version':'discord-synthetic-v1'}, 'independent')
        task = generated_task(base, recipe, 731)
    else:
        task = tutorial_task(representative_task(difficulty=level))
    return dict(task=task, practice='sql', data_version='template-fixture', owner_user_id='owner',
        session_id='template-fixture', guild_id='guild', state='analysis', executions=[], reports=[],
        sql_attempts=[], evaluations=[], sql_checks=[{'schema_name':'PRIVATE_SCHEMA_SENTINEL'}])


def resume_response(document):
    store = Mock()
    store.list.return_value = [document]
    store.get.return_value = document
    provider = Mock()
    service = DiscordTrainingService(store, None, provider, NS())
    response = service.resume('owner', 'guild', document['session_id'])
    provider.review.assert_not_called()
    store.save.assert_not_called()
    return response


@pytest.mark.parametrize('generated', [True, False])
@pytest.mark.parametrize('level,label', [('beginner','초급'), ('intermediate','중급'), ('advanced','고급')])
def test_contract_fields_preserved_for_all_modes_and_levels(generated, level, label):
    doc = sample_document(level, generated)
    before = deepcopy(doc)
    rendered = task_intro(doc)
    assert f'난이도: {label} · 연습 유형: SQL' in rendered
    headings = ['1. 문제 목표','2. 사용할 데이터','3. 계산 조건','4. 출력 형식','5. 제출 방법']
    assert [rendered.index(h) for h in headings] == sorted(rendered.index(h) for h in headings)
    for field in ('goal', 'conditions', 'output', 'extra'):
        for value in doc['task']['sql_intro_sections'][field]:
            assert value in rendered
    assert ', '.join(doc['task']['sql_contract']['columns']) in rendered
    assert 'NULL' in rendered and '1,900자' in rendered and '/sqlrun' in rendered
    if level == 'advanced':
        assert '검산' in rendered and '계획' in rendered
    assert 'PRIVATE_SCHEMA_SENTINEL' not in rendered
    assert doc == before


def test_legacy_and_long_conditions_survive_resume_without_model_calls():
    doc = sample_document('advanced')
    del doc['task']['sql_intro_sections']
    doc['task']['objective'] = '예전 공개 조건 @everyone **원문**\n' * 200
    before = deepcopy(doc)
    response = resume_response(doc)
    rendered = '\n'.join(response['messages'])
    assert doc['task']['objective'] in rendered
    assert '계획' in rendered and response['messages'][-1] == TEMPLATE
    assert response['tables'] == dictionary_tables(doc['task'])
    chunks = safe_chunks(response['messages'][1])
    assert all(len(chunk) <= 1900 and '@everyone' not in chunk for chunk in chunks)
    assert safe_chunks(doc['task']['objective'], 100000)[0] in ''.join(chunks)
    assert doc == before


@pytest.mark.parametrize('fail_images', [False, True])
def test_real_png_or_fallback_between_header_and_conditions_and_prompt_binding(fail_images):
    doc = sample_document('advanced')
    before = deepcopy(doc)
    response = resume_response(doc)
    sent, bindings = [], []
    class Gateway:
        async def send(self, channel, text):
            sent.append(('text', text))
            return NS(id=len(sent))
        async def send_table(self, channel, table):
            if fail_images:
                raise PermissionError('attachments unavailable')
            pages = render_table_png(table)
            assert all(Image.open(BytesIO(page)).width == 1200 for page in pages)
            sent.append(('image', table['title']))
            return [str(len(sent))]
    def bind(*args): bindings.append(args)
    transport = DiscordTransport(NS(bind_sql_prompt=bind), Gateway(), ['guild'])
    asyncio.run(transport._emit(None, response['messages'], doc, response['tables']))
    conditions_index = next(i for i, item in enumerate(sent) if '3. 계산 조건' in item[1])
    assert '2. 사용할 데이터' in sent[0][1]
    for table in response['tables']:
        title = safe_chunks(table['title'])[0] if fail_images else table['title']
        table_index = next(i for i, item in enumerate(sent) if title in item[1])
        assert 0 < table_index < conditions_index
        if fail_images:
            assert safe_chunks(table['rows'][0][0])[0] in sent[table_index][1]
    assert sent[-1] == ('text', TEMPLATE)
    assert len(bindings) == 1 and bindings[0][2] == [str(len(sent))]
    assert doc == before
