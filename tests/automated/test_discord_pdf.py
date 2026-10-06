import asyncio
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

discord = pytest.importorskip('discord')
pytest.importorskip('reportlab')
from da_agent.discord_pdf import pdf_filename, plain_text, render_submission_pdf
from da_agent.discord_pdf_view import ResultPDFView
from da_agent.discord_results import build_submission
from da_agent.errors import DomainError
from test_discord_results import document, setup_forum


def test_pdf_preserves_korean_public_report_held_scores_and_embeds_font():
    pypdf = pytest.importorskip('pypdf')
    doc = document()
    doc['reports'][0]['content']['report_text'] += '\n마지막 줄 <tag> & a_b \\ SQL'
    doc['evaluations'][0]['result'].update(held=True, total=None, reason='평가 서비스 오류')
    sub = build_submission(doc)
    before = deepcopy(sub)
    data = render_submission_pdf(sub)
    reader = pypdf.PdfReader(BytesIO(data))
    text = ''.join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) > 2 and data.startswith(b'%PDF-')
    assert '평가 보류' in text and 'None/100' not in text
    assert '마지막 줄 <tag> & a_b \\ SQL' in text
    assert 'DO_NOT_PUBLISH' not in text
    # Larger body type can wrap inside a phrase; compare content across line breaks.
    content = ''.join(line for line in text.splitlines()
                      if not line.startswith('DA-Result |') and line != 'DA / ANALYSIS REPORT'
                      and not line.strip().isdigit())
    assert ''.join(content.split()).count('긴보고서내용') == 700
    assert '과제ID:session' in ''.join(text.split())
    assert 'https://discord.com/channels/guild/source' not in text
    assert sub == before
    assert b'/FontFile2' in data
    assert '/' not in pdf_filename(sub)
    assert plain_text(r'\*x\* @' + '\u200b' + 'everyone') == '*x* @everyone'


@pytest.mark.parametrize('failure', ['permission', 'generation', 'size'])
def test_pdf_preflight_failure_preserves_results_and_creates_no_post(monkeypatch, failure):
    publisher, parent, user, permissions, posts, _ = setup_forum(monkeypatch)
    doc = document()
    original = deepcopy(doc)
    if failure == 'permission': permissions.attach_files = False
    elif failure == 'generation':
        monkeypatch.setattr('da_agent.discord_forum.render_submission_pdf', Mock(side_effect=ValueError('render error')))
    else: parent.guild.filesize_limit = 1
    save = AsyncMock()
    with pytest.raises(DomainError):
        asyncio.run(publisher.publish(parent, user, build_submission(doc), {}, save))
    assert not posts and doc == original
    save.assert_not_called()


def test_pdf_reused_and_missing_attachment_repaired_without_duplicate_post(monkeypatch):
    publisher, parent, user, _, posts, _ = setup_forum(monkeypatch)
    renderer = Mock(wraps=render_submission_pdf)
    monkeypatch.setattr('da_agent.discord_forum.render_submission_pdf', renderer)
    sub, journal = build_submission(document()), {}
    async def save(**changes): journal.update(changes)
    async def run():
        await publisher.publish(parent, user, sub, journal, save)
        assert posts[0].messages[0].view.is_persistent()
        await publisher.publish(parent, user, sub, journal, save)
        assert renderer.call_count == 1
        posts[0].messages[0].attachments = [NS(filename='keep.txt')]
        await publisher.publish(parent, user, sub, journal, save)
        assert len(posts) == 1 and renderer.call_count == 2
        assert [a.filename for a in posts[0].messages[0].attachments] == ['keep.txt', pdf_filename(sub)]
        assert journal['status'] == 'published'
    asyncio.run(run())


def test_upload_failure_never_marks_publication_complete_and_retry_succeeds(monkeypatch):
    publisher, parent, user, _, posts, _ = setup_forum(monkeypatch)
    sub, journal = build_submission(document()), {}
    async def save(**changes): journal.update(changes)
    async def run():
        forum = await publisher.forum(parent, user)
        create = forum.create_thread
        forum.create_thread = AsyncMock(side_effect=discord.HTTPException(NS(status=403, reason='denied'), 'denied'))
        with pytest.raises(discord.HTTPException):
            await publisher.publish(parent, user, sub, journal, save)
        assert journal['status'] == 'failed' and not posts
        forum.create_thread = create
        await publisher.publish(parent, user, sub, journal, save)
        assert journal['status'] == 'published' and len(posts) == 1
    asyncio.run(run())


def test_bot_setup_registers_restart_safe_download(monkeypatch):
    from da_agent.discord_bot import create_client
    settings = NS(guild_ids=(1,), message_content=False)
    client = create_client(Mock(), settings)
    monkeypatch.setattr(client.da_command_tree, 'sync', AsyncMock())
    async def run():
        await client.setup_hook()
        assert any(isinstance(v, ResultPDFView) and v.is_persistent() for v in client.persistent_views)
        await client.close()
    asyncio.run(run())


def test_visual_pdf_uses_selected_stored_results_without_sql_or_unrelated_data(monkeypatch):
    import json
    from da_agent.discord_tables import render_table_png
    from da_agent.discord_pdf_evidence import display_reference_text
    pypdf = pytest.importorskip('pypdf')
    doc = document()
    ident = 'a5373a79-59c7-4baf-8777-045c83b2b8ef'
    doc['reports'][0]['evidence_refs'] = [ident]
    doc['reports'][0]['content']['report_text'] = '발견한 사실: ads가 낮습니다. 근거 조회 ID: ' + ident
    execution = dict(execution_id=ident, sql='DO_NOT_PUBLISH_SQL', private='DO_NOT_PUBLISH',
                     conditions=dict(metric='tutorial_rate', group_by='channel', step=3,
                                     numerator='completed_users', denominator='signup_users'),
                     result=dict(status='success', columns=[{'name': n} for n in
                                 ['group_value', 'denominator', 'numerator', 'rate_percent']],
                                 rows=[['ads', 19, 13, '68.4210526315789474'],
                                       ['organic', 21, 17, '80.9523809523809524']], truncated=False))
    doc['executions'] = [execution, {**deepcopy(execution), 'execution_id': 'unselected',
                                     'result': {'status': 'success', 'rows': [['PRIVATE_OTHER_RESULT']]}}]
    before = deepcopy(doc)
    submission = build_submission(doc)
    assert doc == before and len(submission['evidence_results']) == 1
    assert 'DO_NOT_PUBLISH' not in json.dumps(submission)
    assert 'PRIVATE_OTHER_RESULT' not in json.dumps(submission)
    render = Mock(wraps=render_table_png)
    monkeypatch.setattr('da_agent.discord_tables.render_table_png', render)
    reader = pypdf.PdfReader(BytesIO(render_submission_pdf(submission)))
    extracted = ''.join(page.extract_text() for page in reader.pages)
    assert ident not in extracted and '조회 결과 1' in extracted
    assert '68.42%' in extracted and '80.95%' in extracted
    assert render.call_args.args[0]['rows'] == [['ads', 19, 13, '68.42'], ['organic', 21, 17, '80.95']]
    assert '가입자 (명)' in render.call_args.args[0]['columns']
    assert any(page.images for page in reader.pages)
    assert display_reference_text('저장 조회 ID: ' + ident, submission['evidence_results']) == '근거: 조회 결과 1'
    submission['evidence_results'][0]['rows'][0][1] = 999
    assert doc['executions'][0]['result']['rows'][0][1] == 19


def test_visual_pdf_discloses_truncated_results_and_preserves_long_table_cells():
    pypdf = pytest.importorskip('pypdf')
    sub = build_submission(document())
    sub['evidence_results'] = [dict(execution_id='saved-query', columns=[{'name': '설명'}],
                                   rows=[['장문 결과 ' * 350 + '표 마지막 내용']],
                                   conditions={}, truncated=True, total_row_count=20)]
    reader = pypdf.PdfReader(BytesIO(render_submission_pdf(sub)))
    text = ''.join(page.extract_text() for page in reader.pages)
    assert '일부 결과만 저장됨' in text and '표 마지막 내용' in text


def test_evaluation_citations_render_new_query_once_and_link_previously_shown_figures(monkeypatch):
    from da_agent.discord_tables import render_table_png
    pypdf = pytest.importorskip('pypdf')
    doc = document()
    doc['reports'][0]['content']['report_text'] = '분석 본문입니다.'
    doc['reports'][0]['evidence_refs'] = ['report-query']
    def query(ident, category):
        return dict(execution_id=ident, conditions={},
                    result=dict(status='success', columns=[{'name': 'group_value'}, {'name': 'rate_percent'}],
                                rows=[[category, 55]], truncated=False))
    doc['executions'] = [query('report-query', 'report-category'), query('evaluation-query', 'evaluation-category'),
                         query('not-cited', 'PRIVATE_UNRELATED')]
    rows = doc['evaluations'][0]['result']['criteria']
    rows[0]['evidence_refs'] = ['execution:report-query', 'execution:evaluation-query', 'execution:evaluation-query']
    rows[1]['evidence_refs'] = ['execution:evaluation-query', 'report:1']
    rows[2]['evidence_refs'] = ['execution:missing-result', 'message:missing-message']
    doc['messages'] = [{'id': 'cited-message', 'role': 'user', 'text': '인용 원문을 실제로 표시합니다.'},
                       {'id': 'uncited-message', 'role': 'user', 'text': 'PRIVATE_UNRELATED_MESSAGE'}]
    rows[3]['evidence_refs'] = ['message:cited-message']
    rows[4]['evidence_refs'] = ['message:cited-message']
    original = deepcopy(doc)
    submission = build_submission(doc)
    assert doc == original
    assert [e['placement'] for e in submission['evidence_results']] == ['report', 'evaluation']
    assert len(submission['message_sources']) == 1
    renderer = Mock(wraps=render_table_png)
    monkeypatch.setattr('da_agent.discord_tables.render_table_png', renderer)
    reader = pypdf.PdfReader(BytesIO(render_submission_pdf(submission)))
    text = ''.join(page.extract_text() for page in reader.pages)
    assert renderer.call_count == 2  # repeated citations never redraw the same result
    assert '앞의 조회 결과 1 그래프·표 참조' in text
    assert '앞의 조회 결과 2 그래프·표 참조' in text
    assert '앞의 제출 보고서 버전 1 참조' in text
    assert '인용한 조회 결과를 확인할 수 없습니다' in text
    assert '인용한 과제 대화를 확인할 수 없습니다' in text
    assert text.count('인용 원문을 실제로 표시합니다.') == 1
    assert '앞의 과제 대화 근거 1 참조' in text
    assert 'PRIVATE_UNRELATED' not in text
    annotations = [a.get_object() for p in reader.pages for a in p.get('/Annots', [])]
    assert sum('/Dest' in a for a in annotations) >= 4


@pytest.mark.parametrize('case', ['success', 'forbidden', 'missing', 'foreign', 'server', 'http'])
def test_persistent_download_fetches_fresh_attachment_and_checks_access(monkeypatch, case):
    class Forum: pass
    class Thread:
        parent = Forum()
        def permissions_for(self, user): return NS(view_channel=case != 'forbidden', read_message_history=True)
    monkeypatch.setattr(discord, 'ForumChannel', Forum)
    monkeypatch.setattr(discord, 'Thread', Thread)
    file = discord.File(BytesIO(b'%PDF-example'), filename='analysis-test.pdf')
    attachment = NS(filename='analysis-test.pdf', to_file=AsyncMock(return_value=file))
    message = NS(author=NS(id=9 if case == 'foreign' else 2), attachments=[] if case == 'missing' else [attachment])
    channel = Thread()
    channel.fetch_message = AsyncMock(return_value=message)
    if case == 'http':
        channel.fetch_message.side_effect = discord.NotFound(NS(status=404, reason='missing'), 'missing')
    event = NS(channel=channel, guild=NS(id=99 if case == 'server' else 1), user=NS(id=3),
               client=NS(user=NS(id=2)), message=NS(id=4), response=NS(defer=AsyncMock()),
               followup=NS(send=AsyncMock()))
    async def run():
        view = ResultPDFView([1])
        assert view.is_persistent()
        await view.children[0].callback(event)
    asyncio.run(run())
    kwargs = event.followup.send.call_args.kwargs
    assert kwargs['ephemeral'] is True
    if case == 'success':
        assert kwargs['file'] is file
        attachment.to_file.assert_awaited_once()
        assert kwargs['allowed_mentions'].everyone is False
    else:
        assert 'file' not in kwargs
        attachment.to_file.assert_not_called()
