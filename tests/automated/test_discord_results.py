import asyncio
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
discord = pytest.importorskip('discord')

from da_agent.discord_education import representative_task
from da_agent.discord_forum import ResultForumPublisher, embed_bytes, MAX_EMBED_BYTES
from da_agent.discord_results import build_submission, card_marker, legacy_card_marker
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_transport import DiscordTransport
from da_agent.errors import DomainError


def document():
    task = representative_task()
    result = dict(total=75, held=False, human_review='pending', recommendation={'practice_criteria': []},
                  criteria=[dict(id=c['id'], grade=3, reason='실행 근거와 한계를 제시했습니다.',
                                 improvement='다른 채널에서도 확인하세요.', evidence_refs=['report:1']) for c in task['rubric']['criteria']])
    return dict(session_id='session', owner_user_id='owner', guild_id='guild', thread_id='source', state='completed',
                task={**task, 'private_generation': 'DO_NOT_PUBLISH'}, reports=[dict(id='report', version=1,
                content={'report_text': '@everyone **제출 보고서**\n' + '긴 보고서 내용 ' * 700},
                followup_answers=[{'text': '추가 로그를 확인합니다.'}])], evaluations=[dict(id='evaluation', report_id='report',
                at='2026-10-06T00:00:00Z', result=result)], learning=[], queries=[], executions=[], messages=[], telemetry=[])


class Store:
    def __init__(self, doc): self.document, self.events = doc, {}
    def get(self, owner, sid):
        if owner != self.document['owner_user_id']: raise DomainError('owner', '자신의 기록만')
        return self.document
    def claim_event(self, event, *args): return self.events.get(event)
    def finish_event(self, event, response, *args): self.events[event] = deepcopy(response)
    def list(self, *args): return [self.document]
    @contextmanager
    def edit(self, owner, sid):
        self.get(owner, sid)
        yield self.document, None


def test_readable_cards_preserve_full_report_and_public_scores_without_private_json():
    doc = document()
    original = deepcopy(doc)
    submission = build_submission(doc)
    report_cards = [c for c in submission['cards'] if c['title'].startswith('제출 보고서')]
    assert len(report_cards) > 1
    assert len(submission['post_name']) <= 100
    assert all(len(c['description']) <= 1900 for c in submission['cards'])
    text = '\n'.join(c['description'] for c in submission['cards'])
    assert '75/100' in text and 'DO_NOT_PUBLISH' not in text and '"criteria"' not in text
    assert '@everyone' not in text and '추가 로그를 확인합니다' in text
    assert all(any(c['name'] in item['title'] and '등급 3/4' in item['title'] for item in submission['cards']) for c in doc['task']['rubric']['criteria'])
    assert doc == original


def test_verification_and_revision_cards_display_scope_errors_and_holds():
    from da_agent.discord_verification import verify_report
    from test_discord_verification import evidence
    doc = document()
    doc['reports'][0]['content'] = {'report_text':'구성 변화만으로 전체 하락을 완전히 설명한다.'}
    doc['reports'][0]['evidence_refs'] = ['q1','q2']
    doc['evaluations'][0]['result']['arithmetic_verification'] = verify_report(doc['task'], doc['reports'][0], evidence())
    doc['evaluations'][0]['revision_comparison'] = dict(previous_evaluation_id='previous', previous_report_version=1,
        report_version=2, criteria=[{'id':'evidence_interpretation','before':1,'after':None}],
        errors_before=1, errors_after=0, previous_total=85, total=None, note='학습 효과 입증은 아님')
    cards = build_submission(doc)['cards']
    text = '\n'.join(c['title'] + c['description'] for c in cards)
    assert '실행 근거 검산' in text and '80.0000%' in text and 'execution:q1' in text
    assert '미검산 표현은 정답 확인' in text
    assert '이전 제출과 수정 비교' in text and '1 → 보류' in text and '85 → 보류' in text


def test_held_evaluation_does_not_claim_score_or_growth():
    doc = document()
    doc['evaluations'][0]['result'].update(held=True, total=None, reason='평가 서비스 오류')
    for row in doc['evaluations'][0]['result']['criteria']:
        row.update(grade=None, held_reason='평가 서비스 오류')
    submission = build_submission(doc)
    text = '\n'.join(c['title'] + c['description'] for c in submission['cards'])
    assert '평가 보류' in text and '판정 보류' in text
    assert 'None/100' not in text and '성장 판단 자료 부족' in text


def test_completed_submit_republishes_without_another_evaluation_or_score_change():
    store, provider = Store(document()), Mock()
    service = DiscordTrainingService(store, None, provider, NS())
    response = service.handle('owner', 'session', 'retry', 'submit')
    assert response['submission']['evaluation_id'] == 'evaluation'
    assert len(store.document['evaluations']) == 1
    provider.review.assert_not_called()
    assert response == service.handle('owner', 'session', 'retry', 'submit')
    assert service.resume('owner', 'guild', 'session')['submission']['evaluation_id'] == 'evaluation'


@pytest.mark.parametrize('held', [False, True])
def test_first_submit_saves_readable_result_even_when_evaluation_is_held(monkeypatch, held):
    doc = document()
    result = deepcopy(doc['evaluations'][0]['result'])
    result.update(held=held, total=None if held else 75)
    doc.update(state='reporting', evaluations=[], learning=[], help_history=[], difficulty='intermediate')
    evaluator = Mock(return_value=result)
    monkeypatch.setattr('da_agent.discord_education.evaluate_report', evaluator)
    from discord_test_quality import ScriptedQualityRegistry
    service = DiscordTrainingService(Store(doc), None, Mock(), NS(),quality_registry=ScriptedQualityRegistry())
    response = service.handle('owner', 'session', 'first-submit', 'submit')
    assert response['submission']['evaluation_id'] == doc['evaluations'][0]['id']
    assert doc['state'] == ('reporting' if held else 'completed')
    assert '"criteria"' not in response['messages'][0]
    evaluator.assert_called_once()


def test_publication_journal_checks_owner_and_prevents_concurrent_creation():
    service = DiscordTrainingService(Store(document()), None, None, NS())
    service.save_result_publication('owner', 'session', 'evaluation', status='creating', forum_id='forum')
    with pytest.raises(DomainError, match='다른 요청'):
        service.save_result_publication('owner', 'session', 'evaluation', status='creating')
    with pytest.raises(DomainError):
        service.result_publication('other', 'session', 'evaluation')
    service.save_result_publication('owner', 'session', 'evaluation', status='partial', post_id='post')
    assert service.result_publication('owner', 'session', 'evaluation')['post_id'] == 'post'


def test_profile_hold_does_not_claim_missing_learner_evidence_or_failed_response():
    doc=document()
    result=doc['evaluations'][0]['result']
    result.update(held=True,total=None,profile_score_hold=True,
        quality_profile={'status':'held','reasons':['유형 검증 미통과']},
        quality_validation={'status':'passed','version':'evaluation-quality-v1','issues':[]},
        recommendation={'practice_criteria':[]})
    doc['evaluations'][0]['revision_feedback'] = dict(note='수정 관측',resolved=[],remaining=[],new=[],unverified=[])
    text='\n'.join(c['description'] for c in build_submission(doc)['cards'])
    assert '유형의 반복 품질 검증 대기' in text
    assert '평가 응답을 확인하지 못한 시스템 보류' not in text
    assert '오류 수정은 확인했습니다' in text and '남은 오류를 수정' not in text
    result['provider_failure']={'reason':'api_rate_limited','provider_diagnostic':{'raw':'DO_NOT_DISPLAY'}}
    text='\n'.join(c['description'] for c in build_submission(doc)['cards'])
    assert '호출 한도 또는 요청 빈도 제한' in text and 'DO_NOT_DISPLAY' not in text
    for row in result['criteria']: row.update(grade=None,improvement='')
    text='\n'.join(c['description'] for c in build_submission(doc)['cards'])
    assert '공급자 실패를 학습자 근거 부족으로 감점하지 않습니다' in text
    assert '판정 가능한 근거를 보완' not in text


def setup_forum(monkeypatch, exists=True):
    permissions = NS(view_channel=True, read_message_history=True, embed_links=True,
                     send_messages=True, send_messages_in_threads=True, manage_channels=True, manage_threads=True, attach_files=True)
    user = NS(id=1, display_name='참가자')
    bot = NS(id=2)
    posts = []
    class Message:
        def __init__(self, embed, file=None, view=None):
            self.author, self.embeds = bot, [embed]
            self.attachments = [NS(filename=file.filename)] if file else []
            self.view = view
        async def edit(self, **kwargs):
            if getattr(self, 'fail_edit', False):
                self.fail_edit = False
                raise ConnectionError('interrupted starter update')
            if 'embed' in kwargs: self.embeds = [kwargs['embed']]
            if 'embeds' in kwargs: self.embeds = kwargs['embeds']
            if 'attachments' in kwargs: self.attachments = [NS(filename=a.filename) for a in kwargs['attachments']]
            if 'view' in kwargs: self.view = kwargs['view']
    class Thread:
        def __init__(self, name, embed, parent, file=None, view=None):
            self.id, self.name, self.parent_id, self.archived = len(posts) + 10, name, parent, False
            self.messages = [Message(embed, file, view)]
            self.fail_send = False
        async def fetch_message(self, ident): return self.messages[0]
        async def history(self, **kwargs):
            for message in self.messages: yield message
        async def send(self, *, embed, **kwargs):
            if self.fail_send:
                self.fail_send = False
                raise ConnectionError('interrupted upload')
            assert kwargs['allowed_mentions'].everyone is False
            self.messages.append(Message(embed))
        async def edit(self, **kwargs): self.archived = kwargs.get('archived', self.archived)
    class Forum:
        def __init__(self, name='DA-Result'):
            self.id, self.name, self.guild = 5, name, guild
            self.flags, self.available_tags = NS(require_tag=False), []
        def permissions_for(self, member): return permissions
        async def archived_threads(self, **kwargs):
            for thread in posts:
                if thread.archived: yield thread
        async def create_thread(self, **kwargs):
            embeds = kwargs.get('embeds') or [kwargs['embed']]
            assert len(embeds) <= 10 and sum(len(e) for e in embeds) <= 6000
            thread = Thread(kwargs['name'], embeds[0], self.id, kwargs.get('file'), kwargs.get('view'))
            thread.messages[0].embeds = embeds
            posts.append(thread)
            return NS(thread=thread)
    async def active_threads(): return [t for t in posts if not t.archived]
    channels = []
    async def fetch_channels(): return channels
    created = []
    async def create_forum(name, **kwargs):
        created.append((name, kwargs))
        forum = Forum(name)
        channels.append(forum)
        return forum
    guild = NS(id='guild', me=bot, fetch_channels=fetch_channels, active_threads=active_threads, create_forum=create_forum, filesize_limit=10 * 1024 * 1024)
    if exists: channels.append(Forum())
    parent = NS(guild=guild, category=NS(id=7), overwrites={'role': 'same permissions'}, permissions_for=lambda user: permissions)
    async def fetch_channel(ident): return next(t for t in posts if t.id == ident)
    client = NS(user=bot, fetch_channel=fetch_channel)
    monkeypatch.setattr(discord, 'ForumChannel', Forum)
    monkeypatch.setattr(discord, 'Thread', Thread)
    return ResultForumPublisher(client), parent, user, permissions, posts, created


def test_many_full_sections_use_one_compact_starter_and_same_pdf(monkeypatch):
    publisher, parent, user, _, posts, _ = setup_forum(monkeypatch)
    doc = document()
    doc['reports'][0]['content'] = {'report_text': '짧은 보고서 원문'}
    sub = build_submission(doc)
    sub['cards'].extend(dict(title=f'추가 평가 {i}', description=f'항목별 평가 근거 {i}') for i in range(15))
    sub['cards'][0]['source_url'] = 'https://discord.com/channels/guild/source'
    before = deepcopy(sub)
    journal = {}
    async def save(**changes): journal.update(changes)
    async def run():
        await publisher.publish(parent, user, sub, journal, save)
        await publisher.publish(parent, user, sub, journal, save)
    asyncio.run(run())
    assert len(posts) == len(posts[0].messages) == 1
    embeds = posts[0].messages[0].embeds
    text = '\n'.join(e.description for e in embeds)
    assert len(embeds) == 1 and len(embeds[0].description) <= 900
    assert '짧은 보고서 원문' in text and '**평가**' in text
    assert '추가 평가 14' not in text
    assert len(posts[0].messages[0].attachments) == 1
    assert embeds[0].fields[0].value.endswith('/source)')
    assert len(embeds) <= 10 and sum(len(e) for e in embeds) <= 6000
    assert sub == before and journal['status'] == 'published'


@pytest.mark.parametrize('held', [False, True])
def test_overflow_previews_keep_evaluation_and_fit_actual_embed_budget(monkeypatch, held):
    publisher, _, user, _, _, _ = setup_forum(monkeypatch)
    doc = document()
    doc['evaluations'][0]['result'].update(held=held, total=None if held else 75)
    sub = build_submission(doc)
    user.display_name = '참가자' * 100
    embeds = publisher.post_embeds(sub, user)
    text = '\n'.join(e.description for e in embeds)
    assert len(embeds) <= 10 and sum(len(e) for e in embeds) <= 6000
    assert all(len(e.description) <= 4096 for e in embeds)
    assert len(embeds) == 1 and len(embeds[0].description) <= 900
    assert '**평가**' in text and 'None/100' not in text
    assert len(sub['cards']) > 1
    if held: assert '평가 보류' in text


def test_inline_budget_boundary_and_extremely_many_sections(monkeypatch):
    publisher, _, user, _, _, _ = setup_forum(monkeypatch)
    sub = build_submission(document())
    for size in (1000, 1500, 3500, 3900, 5000, 5800, 6000, 8000):
        sub['cards'] = [dict(title='요약', description='저장됨'),
                        dict(title='평가 근거', description='가' * size)]
        embeds = publisher.post_embeds(sub, user)
        assert sum(len(e) for e in embeds) <= 6000
        assert all(len(e.description) <= 4096 for e in embeds)
        assert len(embeds) == 1 and len(embeds[0].description) <= 900
        assert embed_bytes(embeds) <= MAX_EMBED_BYTES
    sub['cards'].extend(dict(title='평가' + str(i) + '가' * 200, description='근거' * 900) for i in range(100))
    embeds = publisher.post_embeds(sub, user)
    assert len(embeds) <= 10 and sum(len(e) for e in embeds) <= 6000


def test_multibyte_forum_payload_is_previewed_without_altering_full_pdf_cards(monkeypatch):
    publisher, _, user, _, _, _ = setup_forum(monkeypatch)
    sub = build_submission(document())
    sub['cards'] = [dict(title='요약', description='업무 배경과 판단 기준 ' * 140),
                    *[dict(title=f'평가 근거 {i}', description='공개 자료의 분석 근거입니다. ' * 12) for i in range(10)]]
    before = deepcopy(sub)
    embeds = publisher.post_embeds(sub, user)
    assert embed_bytes(embeds) <= MAX_EMBED_BYTES
    assert sum(len(e) for e in embeds) <= 6000
    assert len(embeds) == 1 and len(embeds[0].description) <= 900
    assert len(sub['cards']) == 11
    assert sub == before


@pytest.mark.parametrize('status', ['failed', 'uncertain'])
def test_missing_starter_cannot_bind_an_empty_forum_thread(monkeypatch, status):
    publisher, parent, user, _, posts, _ = setup_forum(monkeypatch)
    sub, journal = build_submission(document()), {}
    async def save(**changes): journal.update(changes)
    async def run():
        forum = await publisher.forum(parent, user)
        orphan = (await forum.create_thread(name=sub['post_name'], embed=publisher.embed(sub,0,user))).thread
        async def missing(_):
            raise discord.NotFound(NS(status=404,reason='Not Found'), {'code':10008,'message':'Unknown Message'})
        orphan.fetch_message = missing
        assert await publisher.find_post(forum,sub) is None
        if status == 'uncertain':
            with pytest.raises(DomainError,match='중복 게시'):
                await publisher.publish(parent,user,sub,{'status':status},save)
            assert len(posts) == 1
        else:
            await publisher.publish(parent,user,sub,{'status':status},save)
            assert len(posts) == 2 and journal['post_id'] != str(orphan.id)
    asyncio.run(run())


@pytest.mark.parametrize('exists', [True, False])
def test_existing_forum_reused_or_created_with_matching_parent_permissions(monkeypatch, exists):
    publisher, parent, user, permissions, posts, created = setup_forum(monkeypatch, exists)
    sub, journal = build_submission(document()), {}
    async def save(**changes): journal.update(changes)
    async def run():
        url = await publisher.publish(parent, user, sub, journal, save)
        count = len(posts[0].messages)
        assert url.endswith('/10')
        await publisher.publish(parent, user, sub, journal, save)
        assert len(posts) == 1 and len(posts[0].messages) == count == 1
        text = '\n'.join(e.description for e in posts[0].messages[0].embeds)
        assert '**평가**' in text and '75/100' in text
        assert len(posts[0].messages[0].embeds) == 1
        assert len(posts[0].messages[0].attachments) == 1
    asyncio.run(run())
    assert journal['status'] == 'published'
    assert bool(created) != exists
    if not exists: assert created[0][1]['overwrites'] is parent.overwrites


def test_permission_failure_creates_no_post(monkeypatch):
    publisher, parent, user, permissions, posts, created = setup_forum(monkeypatch, False)
    permissions.manage_channels = False
    async def save(**changes): raise AssertionError('Must not start posting')
    with pytest.raises(DomainError, match='채널 관리'):
        asyncio.run(publisher.publish(parent, user, build_submission(document()), {}, save))
    assert not posts and not created


def test_partial_post_and_unknown_create_recovered_by_verified_marker(monkeypatch):
    publisher, parent, user, permissions, posts, created = setup_forum(monkeypatch)
    sub, journal = build_submission(document()), {}
    async def save(**changes): journal.update(changes)
    async def run():
        forum = await publisher.forum(parent, user)
        post = (await forum.create_thread(name=sub['post_name'], embed=publisher.embed(sub, 0, user))).thread
        post.messages[0].fail_edit = True
        with pytest.raises(ConnectionError): await publisher.publish(parent, user, sub, {'status': 'creating'}, save)
        assert journal['post_id'] == str(post.id)
        await publisher.publish(parent, user, sub, journal, save)
        assert len(posts) == 1 and len(post.messages) == 1
        post.archived = True
        assert await publisher.find_post(forum, sub) is post
        await publisher.publish(parent, user, sub, {'status': 'uncertain'}, save)
        assert not post.archived and len(posts) == 1
    asyncio.run(run())


def test_unknown_creation_without_matching_post_never_duplicates(monkeypatch):
    publisher, parent, user, permissions, posts, created = setup_forum(monkeypatch)
    async def save(**changes): raise AssertionError('Must not create')
    with pytest.raises(DomainError, match='중복 게시'):
        asyncio.run(publisher.publish(parent, user, build_submission(document()), {'status': 'uncertain'}, save))
    assert not posts


def test_legacy_post_is_consolidated_without_duplicate_messages_or_touching_replies(monkeypatch):
    publisher, parent, user, permissions, posts, created = setup_forum(monkeypatch)
    sub, journal = build_submission(document()), {}
    async def save(**changes): journal.update(changes)
    async def run():
        forum = await publisher.forum(parent, user)
        post = (await forum.create_thread(name=sub['post_name'], embed=publisher.embed(sub, 0, user))).thread
        post.messages[0].embeds[0].set_footer(text=legacy_card_marker(sub, 0))
        for index in range(1, len(sub['cards'])):
            await post.send(embed=publisher.embed(sub, index, user), allowed_mentions=discord.AllowedMentions.none())
        replies = list(post.messages[1:])
        await publisher.publish(parent, user, sub, journal, save)
        assert len(posts[0].messages) == len(sub['cards'])
        assert post.messages[1:] == replies
        assert post.messages[0].embeds[0].footer.text == card_marker(sub, 0)
        assert '**평가**' in '\n'.join(e.description for e in post.messages[0].embeds)
        assert len(post.messages[0].attachments) == 1
    asyncio.run(run())


def test_transport_failed_publication_preserves_evaluation_and_falls_back_readably():
    doc = document()
    original = deepcopy(doc['evaluations'])
    service = DiscordTrainingService(Store(doc), None, None, NS())
    class Gateway:
        def __init__(self): self.messages, self.cards = [], []
        async def publish_result(self, *args): raise DomainError('permissions', '포럼 발언 권한을 확인하세요.')
        async def send(self, channel, text): self.messages.append(text)
        async def send_result_card(self, channel, sub, index, user): self.cards.append(sub['cards'][index])
        async def reply(self, event, text): self.messages.append(text)
    gateway = Gateway()
    transport = DiscordTransport(service, gateway, ['guild'])
    event = NS(user=NS(id='owner'), guild=NS(id='guild'))
    asyncio.run(transport._publish_result(event, NS(parent=NS()), build_submission(doc)))
    assert '제출·평가 기록은 저장' in gateway.messages[0]
    assert gateway.cards and doc['evaluations'] == original
    assert service.result_publication('owner', 'session', 'evaluation')['error_code'] == 'permissions'


@pytest.mark.parametrize('failure', [None, 'publish', 'notice', 'archive', 'held'])
def test_archive_only_after_complete_publication_and_destination_notice(failure):
    doc = document()
    if failure == 'held':
        doc['state'] = 'reporting'
        doc['evaluations'][0]['result']['held'] = True
    original = deepcopy(doc['evaluations'])
    service = DiscordTrainingService(Store(doc), None, None, NS())
    parent, channel = NS(id='parent'), NS(id='source', parent=NS(id='parent'))
    calls = []
    class Gateway:
        async def publish_result(self, parent, user, sub, journal, save):
            calls.append('publish')
            if failure == 'publish': raise ConnectionError()
            await save(status='published', post_id='post', forum_id='forum')
            return 'https://discord.com/channels/guild/post'
        async def send(self, target, text):
            calls.append('notice' if target.id == 'parent' else 'fallback')
            if failure == 'notice': raise ConnectionError()
        async def reply(self, event, text):
            calls.append('reply')
            if failure == 'reply' and calls.count('reply') == 1: raise ConnectionError()
        async def send_result_card(self, *args): pass
        async def archive_task_thread(self, target, user):
            calls.append('archive')
            if failure == 'archive': raise ConnectionError()
            target.archived = target.locked = True
    transport = DiscordTransport(service, Gateway(), ['guild'])
    event = NS(user=NS(id='owner'), guild=NS(id='guild'))
    asyncio.run(transport._publish_result(event, channel, build_submission(doc)))
    if failure in (None, 'archive'):
        assert calls[:4] == ['publish', 'fallback', 'notice', 'archive']
    else:
        assert 'archive' not in calls
    if failure in ('notice', 'archive'):
        assert service.result_publication('owner', 'session', 'evaluation')['status'] == 'published'
    assert doc['evaluations'] == original


def test_resume_deleted_completed_thread_reuses_forum_without_creating_task_thread():
    doc = document()
    doc.update(guild_id='10', owner_user_id='1', channel_id='20', thread_id='30')
    service = DiscordTrainingService(Store(doc), None, None, NS())
    service.save_result_publication('1', 'session', 'evaluation', status='published', post_id='40', forum_id='50')
    calls = []
    class Gateway:
        async def defer(self, event): pass
        async def fetch_channel(self, ident):
            calls.append(('fetch', ident))
            return NS(id=20) if ident == 20 else None
        async def validate_parent(self, *args): pass
        async def publish_result(self, parent, user, sub, journal, save):
            assert journal['post_id'] == '40'
            return 'https://discord.com/channels/10/40'
        async def reply(self, event, text): calls.append(('reply', text))
        async def create_private_thread(self, *args): raise AssertionError('Must not recreate')
    event = NS(id=100, user=NS(id=1), guild=NS(id=10), channel=NS(id=20))
    asyncio.run(DiscordTransport(service, Gateway(), ['10']).command(event, 'resume', session_id='session'))
    assert calls[:2] == [('fetch', 30), ('fetch', 20)]
    assert '/10/40' in calls[-1][1] and '다시 만들지' in calls[-1][1]
    assert doc['thread_id'] == '30' and len(doc['evaluations']) == 1


def test_results_use_saved_task_id_instead_of_deleted_thread_link():
    submission = build_submission(document())
    assert '과제 ID: session' in submission['cards'][0]['description']
    assert not any(c.get('source_url') for c in submission['cards'])
