import asyncio
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
discord = pytest.importorskip('discord')

from da_agent.discord_education import representative_task
from da_agent.discord_forum import ResultForumPublisher
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
    service = DiscordTrainingService(Store(doc), None, Mock(), NS())
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
            if 'embed' in kwargs: self.embeds = [kwargs['embed']]
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
            thread = Thread(kwargs['name'], kwargs['embed'], self.id, kwargs.get('file'), kwargs.get('view'))
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
        assert len(posts) == 1 and len(posts[0].messages) == count == len(sub['cards'])
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
        post.fail_send = True
        with pytest.raises(ConnectionError): await publisher.publish(parent, user, sub, {'status': 'creating'}, save)
        assert journal['post_id'] == str(post.id)
        await publisher.publish(parent, user, sub, journal, save)
        assert len(posts) == 1 and len(post.messages) == len(sub['cards'])
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


def test_legacy_long_footer_cards_are_updated_without_duplicate_messages(monkeypatch):
    publisher, parent, user, permissions, posts, created = setup_forum(monkeypatch)
    sub, journal = build_submission(document()), {}
    async def save(**changes): journal.update(changes)
    async def run():
        await publisher.publish(parent, user, sub, journal, save)
        for index, message in enumerate(posts[0].messages):
            message.embeds[0].set_footer(text=legacy_card_marker(sub, index))
        await publisher.publish(parent, user, sub, journal, save)
        assert len(posts[0].messages) == len(sub['cards'])
        assert all(message.embeds[0].footer.text == card_marker(sub, i) for i, message in enumerate(posts[0].messages))
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
    gateway = Gateway()
    transport = DiscordTransport(service, gateway, ['guild'])
    event = NS(user=NS(id='owner'), guild=NS(id='guild'))
    asyncio.run(transport._publish_result(event, NS(parent=NS()), build_submission(doc)))
    assert '제출·평가 기록은 저장' in gateway.messages[0]
    assert gateway.cards and doc['evaluations'] == original
    assert service.result_publication('owner', 'session', 'evaluation')['error_code'] == 'permissions'
