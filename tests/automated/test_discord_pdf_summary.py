import asyncio
import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

discord = pytest.importorskip('discord')
pypdf = pytest.importorskip('pypdf')
pytest.importorskip('reportlab')
from da_agent.discord_pdf import pdf_filename, render_submission_pdf
from da_agent.discord_pdf_summary import _excerpt, _table, render_summary_pdf, summary_data, summary_filename
from da_agent.discord_pdf_view import ResultPDFView
from da_agent.discord_results import build_submission
from test_discord_results import document, setup_forum


def public_submission():
    doc = document()
    doc['reports'][0]['content'] = {
        'findings': '커뮤니티 완료율은 50%, 검색 광고는 40%입니다. 이 차이는 채널의 인과 효과가 아닙니다.',
        'action': '미완료 규모가 큰 집단을 먼저 조사합니다.',
        'quality': '중복과 누락은 0개입니다. 미시작 계정도 분모에 포함합니다.',
        'limitations': '합성 자료이며 서비스 효과를 확정할 수 없습니다.',
    }
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/result-pdf-preview-evidence.json').read_text(encoding='utf-8'))
    sub = build_submission(doc)
    sub['evidence_results'] = [dict(e, execution_id=f'query-{i}', conditions={}, placement='report')
                               for i, e in enumerate(fixture['evidence_results'], 1)]
    return sub


def test_three_learning_pages_preserve_stored_values_and_qualified_sentences():
    sub = public_submission()
    sub['cards'][0].update(held=True, score=None)
    sub['cards'][0]['description'] = sub['cards'][0]['description'].replace('평가 점수: 75/100', '평가 보류: 반복 품질 검증 대기')
    before = deepcopy(sub)
    data = summary_data(sub)
    assert data['metrics']['rows'][0] == ['커뮤니티', '100', '50', '50', '50']
    assert data['details']['rows'][-1] == ['검색 광고', '경험 적음', '20 / 80', '25', '60']
    assert '인과 효과가 아닙니다.' in data['conclusion']
    reader = pypdf.PdfReader(BytesIO(render_summary_pdf(sub)))
    assert len(reader.pages) == 3
    texts = [p.extract_text() for p in reader.pages]
    assert '제출 보고 핵심 발췌' in texts[0]
    assert '분석 근거' in texts[1]
    assert '평가 피드백' in texts[2] and '평가 보류' in texts[2]
    assert '75/100' not in ''.join(texts) and 'DO_NOT_PUBLISH' not in ''.join(texts)
    assert sub == before and b'/FontFile2' in render_summary_pdf(sub)


def test_excerpt_never_drops_the_end_of_a_long_qualifying_sentence():
    source = '원인이 채널 효과라는 매우 긴 주장 ' * 80 + '이라고 확정할 수 없습니다.'
    assert _excerpt(source, '채널', 100) == '이 항목은 상세 원문 PDF에서 확인하세요.'


def test_real_e2e_report_keeps_both_channel_rows_and_all_four_experience_rows():
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/result-pdf-preview-evidence.json').read_text(encoding='utf-8'))
    doc = document()
    doc['task'].update(title=fixture['real_title'], period=fixture['real_period'], timezone='Asia/Seoul')
    doc['reports'][0]['content'] = {'report_text': fixture['real_report_text']}
    sub = build_submission(doc)
    sub['evidence_results'] = public_submission()['evidence_results']
    reader = pypdf.PdfReader(BytesIO(render_summary_pdf(sub)))
    assert len(reader.pages) == 3
    first, second = [page.extract_text() for page in reader.pages[:2]]
    assert '저장 2행 중 2행' in first and '저장 4행 중 4행' in second
    assert '분량 제한으로 발췌를 더 줄였습니다' not in ''.join(p.extract_text() for p in reader.pages)


def test_unknown_ratio_units_duplicate_columns_and_truncation_are_not_invented():
    query = dict(columns=[{'name': n} for n in ['total_account_count', 'completed_account_count', 'completion_rate']],
                 rows=[[100, 50, 50]], truncated=True, total_row_count=20)
    table = _table(query, 1, 3)
    assert table['headers'][-1] == '완료율 (원값)' and table['rows'][0][-1] == '50'
    assert '일부 결과만 저장됨' in table['caption']
    duplicate = dict(columns=['same', 'same'], rows=[['first', 'second']])
    assert _table(duplicate, 1, 3)['rows'] == [['first', 'second']]


def test_large_generic_tables_feedback_and_report_stay_readable_with_full_detail_available():
    sub = public_submission()
    sub['cards'][0]['description'] = '아주 긴 제목 ' * 50 + '\n평가 보류: 상태 확인 필요'
    sub['summary_context']['title'] = '긴 제목 ' * 50
    for card in sub['cards'][1:]:
        if card.get('pdf_kind') == 'report':
            card['description'] = '한계와 인과 효과의 설명 ' * 2000
    sub['evidence_results'] = [dict(execution_id='wide-query', columns=[{'name': '긴 열 이름 ' * 20} for _ in range(9)],
                                  rows=[['긴 셀 내용 ' * 100] * 9 for _ in range(100)], conditions={}, truncated=True, total_row_count=200)]
    summary = pypdf.PdfReader(BytesIO(render_summary_pdf(sub)))
    assert len(summary.pages) == 3
    text = ''.join(p.extract_text() for p in summary.pages)
    assert '일부 결과만 저장됨' in text and '…' in text and '상세 원문 PDF' in text


def test_long_feedback_chunks_are_one_row_with_the_actual_improvement():
    doc = document()
    for criterion in doc['evaluations'][0]['result']['criteria']:
        criterion['reason'] = '평가 근거 원문 ' * 450
        criterion['improvement'] = '다음에는 비교 조건을 먼저 확인하세요.'
    data = summary_data(build_submission(doc))
    assert len(data['feedback']['rows']) == 5
    assert all('비교 조건을 먼저 확인' in row[2] for row in data['feedback']['rows'])


def test_legacy_starter_gains_summary_once_and_keeps_full_attachment(monkeypatch):
    publisher, parent, user, _, posts, _ = setup_forum(monkeypatch)
    sub, journal = public_submission(), {}
    async def save(**changes): journal.update(changes)
    summary = Mock(wraps=render_summary_pdf)
    full = Mock(wraps=render_submission_pdf)
    monkeypatch.setattr('da_agent.discord_forum.render_summary_pdf', summary)
    monkeypatch.setattr('da_agent.discord_forum.render_submission_pdf', full)
    async def run():
        forum = await publisher.forum(parent, user)
        post = (await forum.create_thread(name=sub['post_name'], embed=publisher.embed(sub, 0, user))).thread
        old = NS(filename=pdf_filename(sub), id='old-full')
        post.messages[0].attachments = [old]
        journal['post_id'] = str(post.id)
        await publisher.publish(parent, user, sub, journal, save)
        await publisher.publish(parent, user, sub, journal, save)
        assert len(posts) == 1 and summary.call_count == 1 and full.call_count == 0
        assert [a.filename for a in post.messages[0].attachments] == [pdf_filename(sub), summary_filename(sub)]
        assert len(post.messages[0].view.children) == 2
    asyncio.run(run())


def test_summary_generation_failure_preserves_record_and_creates_no_post(monkeypatch):
    publisher, parent, user, _, posts, _ = setup_forum(monkeypatch)
    monkeypatch.setattr('da_agent.discord_forum.render_summary_pdf', Mock(side_effect=ValueError('failure')))
    sub = public_submission()
    before = deepcopy(sub)
    from da_agent.errors import DomainError
    with pytest.raises(DomainError, match='PDF 생성에 실패'):
        asyncio.run(publisher.publish(parent, user, sub, {}, AsyncMock()))
    assert sub == before and not posts


@pytest.mark.parametrize('index', [0, 1])
def test_buttons_select_correct_fresh_attachment_when_legacy_full_is_first(monkeypatch, index):
    class Forum: pass
    class Thread:
        parent = Forum()
        def permissions_for(self, user): return NS(view_channel=True, read_message_history=True)
    monkeypatch.setattr(discord, 'ForumChannel', Forum)
    monkeypatch.setattr(discord, 'Thread', Thread)
    attachments = []
    files = []
    for name in ('analysis-123.pdf', 'analysis-summary-v1-123.pdf'):
        file = discord.File(BytesIO(b'%PDF-example'), filename=name)
        files.append(file)
        attachments.append(NS(filename=name, to_file=AsyncMock(return_value=file)))
    event = NS(channel=Thread(), guild=NS(id=1), user=NS(id=3), client=NS(user=NS(id=2)),
               message=NS(id=4), response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
    event.channel.fetch_message = AsyncMock(return_value=NS(author=NS(id=2), attachments=attachments))
    async def run():
        view = ResultPDFView([1])
        await view.children[index].callback(event)
    asyncio.run(run())
    assert event.followup.send.call_args.kwargs['file'] is files[1 if index == 0 else 0]
    assert ResultPDFView([1], include_detail=False).children[0].custom_id == 'da-result:pdf:v1'
