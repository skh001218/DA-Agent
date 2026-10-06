import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from da_agent.discord_bot import DiscordSettings, create_client
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_thread_titles import reserve_title, thread_title, thread_title_base
from da_agent.discord_transport import DiscordTransport
from test_discord_results import document, Store


def test_short_names_distinguish_difficulty_and_repeated_truncated_prefixes():
    first = document()
    first['difficulty'] = 'intermediate'
    first['task']['title'] = '튜토리얼 완료율 변화와 우선 대응'
    reserve_title(first, [])
    assert first['thread_name'] == '[중급 - 튜토리얼 완료율 변화와 우선 대응]'
    second = deepcopy(first)
    for key in ('thread_name', 'thread_name_base', 'thread_name_ordinal'):
        second.pop(key)
    reserve_title(second, [first])
    assert second['thread_name'] == '[중급 #2 - 튜토리얼 완료율 변화와 우선 대응]'
    reserve_title(second, [first, second])
    assert second['thread_name_ordinal'] == 2
    third = deepcopy(document())
    third['difficulty'] = 'advanced'
    reserve_title(third, [first, second])
    assert third['thread_name'].startswith('[고급 - ') and '#' not in third['thread_name']
    long = deepcopy(document())
    long['task']['title'] = '같은 긴 공개 과제 설명 ' * 20 + 'A'
    reserve_title(long, [])
    other = deepcopy(document())
    other['task']['title'] = '같은 긴 공개 과제 설명 ' * 20 + 'B'
    reserve_title(other, [long])
    assert '…' in long['thread_name'] and '[중급 #2 - ' in other['thread_name']
    assert len(other['thread_name']) <= 40


def test_name_normalizes_linebreaks_controls_mentions_and_brackets():
    doc = {'difficulty': 'beginner', 'task': {'title': '  @everyone\n[매출]\u200b   분석  '}}
    assert thread_title(thread_title_base(doc)) == '[초급 - everyone 매출 분석]'
    assert thread_title(thread_title_base({})) == '[연습 - 분석 연습]'


def test_archived_completed_resume_never_reopens_or_posts_to_task():
    doc = document()
    doc.update(guild_id='10', owner_user_id='1', channel_id='20', thread_id='30')
    service = DiscordTrainingService(Store(doc), None, None, NS())
    service.save_result_publication('1', 'session', 'evaluation', status='published', post_id='40', forum_id='50')
    channel = NS(id=30, parent=NS(id=20), archived=True, locked=True)
    calls = []
    class Gateway:
        async def defer(self, event): pass
        async def fetch_channel(self, ident): return channel
        async def validate_thread(self, channel, user, *, reopen=True):
            assert reopen is False
            calls.append('read')
        async def publish_result(self, parent, user, sub, journal, save):
            assert journal['post_id'] == '40'
            return 'https://discord.com/channels/10/40'
        async def archive_task_thread(self, channel, user):
            assert channel.archived and channel.locked
        async def send(self, *args): raise AssertionError('No repeated notices')
        async def reply(self, event, text): calls.append(text)
        async def create_private_thread(self, *args, **kwargs): raise AssertionError('No recreation')
    event = NS(id=100, user=NS(id=1), guild=NS(id=10), channel=NS(id=20))
    asyncio.run(DiscordTransport(service, Gateway(), ['10']).command(event, 'resume', session_id='session'))
    assert 'read' in calls
    assert '/10/30' in calls[-1] and '/10/40' in calls[-1] and '보관·잠금' in calls[-1]
    assert channel.archived and channel.locked


@pytest.mark.parametrize('locked', [False, True])
def test_sdk_archived_validation_and_archive_do_not_unarchive_or_join(tmp_path, monkeypatch, locked):
    discord = pytest.importorskip('discord')
    permissions = NS(view_channel=True, create_private_threads=True, send_messages_in_threads=True,
                     manage_threads=True, read_message_history=True)
    guild = NS(id=10, me=NS(id=99))
    class Parent:
        def __init__(self): self.guild = guild
        def permissions_for(self, member): return permissions
    class Thread:
        invitable = False
        archived = True
        def __init__(self):
            self.guild, self.parent, self.locked, self.edits = guild, Parent(), locked, []
        def is_private(self): return True
        async def join(self): raise AssertionError('No joining archived threads')
        async def fetch_members(self): return [NS(id=1), NS(id=99)]
        async def edit(self, **changes):
            assert changes['archived'] and changes['locked']
            self.edits.append(changes)
            self.locked = True
    monkeypatch.setattr(discord, 'TextChannel', Parent)
    monkeypatch.setattr(discord, 'Thread', Thread)
    async def run():
        client = create_client(NS(), DiscordSettings('fake', (10,), 'r', 'a', 'l', tmp_path / 'key'))
        client._connection.user = NS(id=99)
        channel = Thread()
        await client.da_transport.gateway.validate_thread(channel, NS(id=1), reopen=False)
        assert not channel.edits
        await client.da_transport.gateway.archive_task_thread(channel, NS(id=1))
        assert len(channel.edits) == (0 if locked else 1)
        await client.close()
    asyncio.run(run())


def test_history_paginates_read_only_and_preserves_held_result():
    docs = [deepcopy(document()) for _ in range(6)]
    for index, doc in enumerate(docs):
        doc.update(session_id=f's{index}', guild_id='10', created_at='2026-10-06T00:00:00Z')
        doc['result_publications'] = {'evaluation': {'status': 'published', 'post_id': str(100 + index)}}
    docs[0]['evaluations'][0]['result'].update(held=True, total=None)
    store = NS(list=lambda owner, guild: docs)
    service = DiscordTrainingService(store, None, None, NS())
    original = deepcopy(docs)
    text = service.history('owner', '10')[0]
    assert '1/2페이지' in text and '평가 보류' in text and 'None/100' not in text
    assert '/10/100' in text and '/10/105' not in text and '/history page:2' in text
    assert '/10/105' in service.history('owner', '10', 2)[0]
    assert '총 2페이지' in service.history('owner', '10', 3)[0]
    assert docs == original


def test_history_uses_current_owner_and_server_without_task_or_model():
    calls, replies = [], []
    def history(owner, guild, page):
        calls.append((owner, guild, page))
        return ['[결과 보기](https://discord.com/channels/10/40)']
    class Gateway:
        async def defer(self, event): pass
        async def reply(self, event, text): replies.append(text)
    event = NS(id=100, user=NS(id=1), guild=NS(id=10), channel=NS(id=20))
    asyncio.run(DiscordTransport(NS(history=history), Gateway(), ['10']).command(event, 'history', payload={'page': 2}))
    assert calls == [('1', '10', 2)]
    assert replies == ['[결과 보기](https://discord.com/channels/10/40)']
