import asyncio
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace as NS
from contextlib import contextmanager

import pytest
Image = pytest.importorskip('PIL.Image')

from da_agent.discord_education import representative_task
from da_agent.discord_tables import dictionary_tables, result_table, render_table_png, cell_text, is_dictionary_request
from da_agent.discord_service import DiscordTrainingService, format_result
from da_agent.discord_transport import DiscordTransport


def test_png_preserves_korean_dictionary_metadata_and_numeric_values():
    task = representative_task()
    tables = dictionary_tables(task)
    assert len(tables) == 3
    for name, table in zip(task['dictionary'], tables):
        assert task['dictionary'][name]['description'] in table['subtitle']
        image = Image.open(BytesIO(render_table_png(table)[0]))
        assert image.format == 'PNG' and image.width == 1200
        assert image.height > 300
        assert image.getpixel((34, image.height - 73)) != (244, 247, 251)
    assert cell_text(0) == '0' and cell_text(None) != '0'
    assert cell_text(12345) == '12,345'
    assert cell_text('75.0000000000000000') == '75'
    assert cell_text('0.123456789123456789') == '0.123456789123456789'
    assert cell_text(0.123456789123) == '0.123456789123'


def test_result_preview_does_not_change_saved_evidence_or_hide_null_limits():
    execution = dict(execution_id='exec', conditions={'denominator': 'users'}, data_version='v',
                     result=dict(columns=['분모', '비율'], rows=[[0, None]] * 12, result_complete=False))
    original = deepcopy(execution)
    table = result_table(execution)
    assert len(table['rows']) == 10
    assert Image.open(BytesIO(render_table_png(table)[0])).height > 500
    text = format_result(execution, NS(max_rows=1000, max_bytes=1048576, query_timeout_ms=5000))
    assert 'NULL' in text and '분모 0' in text and '표시만 첫 10행' in text
    assert '전체 건수는 알 수 없습니다' in text
    assert ' | ' not in text and execution == original
    execution['result']['rows'] = []
    assert '빈 결과' in format_result(execution, NS(max_rows=1000, max_bytes=1048576, query_timeout_ms=5000))
    assert render_table_png(result_table(execution))


def test_dictionary_request_needs_no_model_sql_and_keeps_pending_query():
    document = dict(task=representative_task(), state='completed', queries=[], executions=[],
                    messages=[], telemetry=[], pending_query={'text': '완료율'}, session_id='s')
    class Store:
        def get(self, *args): return document
        def claim_event(self, *args): return None
        def finish_event(self, event, response, conn): self.response = response
        @contextmanager
        def edit(self, *args): yield document, None
    store = Store()
    service = DiscordTrainingService(store, None, None, NS())
    response = service.handle('owner', 's', 'event', 'query', text='users 데이터 사전 보여줘')
    assert len(response['tables']) == 1 and response['tables'][0]['title'].endswith('users')
    assert document['pending_query'] == {'text': '완료율'} and not document['queries']
    assert 'request_state' not in response
    assert service.handle('owner', 's', 'event2', 'help', text='데이터 사전 알려줘')['tables']
    assert is_dictionary_request('데이터 사전 보여줘')
    assert not is_dictionary_request('데이터 사전 기준으로 3단계 완료율 계산해줘')


def test_attachment_failure_still_delivers_values_then_next_message():
    class Gateway:
        def __init__(self): self.sent = []
        async def send(self, channel, text): self.sent.append(text)
        async def send_table(self, channel, table): raise PermissionError('no attach permission')
    gateway = Gateway()
    transport = DiscordTransport(None, gateway, [1])
    table = dict(title='조회 결과', columns=['분모', '완료율'], rows=[[40, 75]], after_message=0)
    asyncio.run(transport._emit(None, ['실행 성공', '후속 질문'], [table]))
    assert gateway.sent[0] == '실행 성공' and gateway.sent[-1] == '후속 질문'
    assert '40' in gateway.sent[1] and '75' in gateway.sent[1]


def test_wide_and_long_results_are_paginated_without_mutating_values():
    table = dict(title='긴 조회', subtitle='한글 설명', columns=['긴 컬럼명' * 8] * 5,
                 rows=[['가나다라' * 35] * 5] * 10)
    original = deepcopy(table)
    pages = render_table_png(table)
    assert len(pages) >= 3
    assert all(Image.open(BytesIO(page)).height < 2600 for page in pages)
    assert table == original
