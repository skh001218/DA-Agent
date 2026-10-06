import asyncio
from types import SimpleNamespace as NS

import pytest

from da_agent.discord_bot import DiscordSettings, create_client
from da_agent.discord_transport import DiscordTransport, safe_chunks
from da_agent.errors import DomainError


class Service:
    def __init__(self):
        self.calls = []
        self.session = dict(session_id="s1", owner_user_id="1", guild_id="10", thread_id=None)

    def start(self, *args, **kwargs):
        self.calls.append(("start", args))
        return dict(self.session)

    def bind_thread(self, user, session, thread):
        self.calls.append(("bind", (user, session, thread)))
        self.session["thread_id"] = thread

    def reserve_thread_name(self, user, session):
        from da_agent.discord_thread_titles import reserve_title
        reserve_title(self.session, [])
        return dict(self.session)

    def resume(self, *args):
        return {"session": dict(self.session), "messages": ["업무 담당자: 준비됨"]}

    def get_session(self, user, session):
        self.calls.append(("get", (user, session)))
        return dict(self.session)

    def list_sessions(self, *args):
        return [dict(self.session)]

    def handle(self, *args, **kwargs):
        self.calls.append(("handle", args, kwargs))
        return {"session": dict(self.session), "messages": ["@everyone **결과**" * 250]}


class Gateway:
    def __init__(self):
        self.events, self.sent, self.replies = [], [], []
        self.thread = NS(id=30, private=True)
        self.missing = False
        self.denied = False

    async def defer(self, event):
        self.events.append("defer")

    async def reply(self, event, text):
        self.replies.append(text)

    async def send(self, channel, text):
        self.sent.append((channel.id, text))

    async def validate_parent(self, channel, user):
        self.events.append("parent")
        if self.denied:
            raise DomainError("denied", "권한 부족", 403)

    async def validate_thread(self, channel, user, **kwargs):
        self.events.append("thread")
        if self.denied or not channel.private:
            raise DomainError("denied", "권한 부족", 403)

    async def create_private_thread(self, *args, **kwargs):
        self.events.append("create")
        self.thread.name = kwargs['name']
        return self.thread

    async def fetch_channel(self, *args):
        return None if self.missing else self.thread

    def is_private_thread(self, channel):
        return getattr(channel, "private", False)


def setup(owner=1, channel=20, guild=10):
    service, gateway = Service(), Gateway()
    transport = DiscordTransport(service, gateway, [10])
    event = NS(id=100, user=NS(id=owner, bot=False), channel=NS(id=channel, private=channel == 30), guild=NS(id=guild))
    return service, gateway, transport, event


def test_start_acknowledges_before_permissions_creates_private_binding():
    service, gateway, transport, event = setup()
    asyncio.run(transport.command(event, "training"))
    assert gateway.events[0] == "defer"
    assert "create" in gateway.events
    assert service.session["thread_id"] == "30"
    assert gateway.sent[0][0] == 30


def test_concurrent_starts_reuse_latest_thread_binding():
    service, gateway, transport, event = setup()
    async def both():
        await asyncio.gather(transport.command(event, 'training'), transport.command(event, 'training'))
    asyncio.run(both())
    assert gateway.events.count('create') == 1


def test_failed_start_creates_no_thread_or_binding():
    service, gateway, transport, event = setup()
    service.start = lambda *args, **kwargs: {"state": "failed", "messages": ["과제 준비 실패"]}
    asyncio.run(transport.command(event, "training"))
    assert "create" not in gateway.events and not gateway.sent
    assert gateway.replies == ["과제 준비 실패"]


def test_resume_interrupted_session_calls_continue_after_permissions():
    service, gateway, transport, event = setup(channel=30)
    service.session.update(thread_id="30", state="interrupted")
    asyncio.run(transport.command(event, "resume", session_id="s1"))
    call = next(call for call in service.calls if call[0] == "handle")
    assert call[1][-1] == "continue"
    assert "thread" in gateway.events


@pytest.mark.parametrize("guild,denied", [(99, False), (10, True)])
def test_fail_closed_before_service_start(guild, denied):
    service, gateway, transport, event = setup(guild=guild)
    gateway.denied = denied
    asyncio.run(transport.command(event, "training"))
    assert not service.calls and not gateway.sent
    assert gateway.replies


@pytest.mark.parametrize("owner,channel", [(2, 30), (1, 20)])
def test_foreign_owner_or_wrong_thread_never_handles(owner, channel):
    service, gateway, transport, event = setup(owner, channel)
    service.session["thread_id"] = "30"
    asyncio.run(transport.command(event, "query", session_id="s1", text="조회"))
    assert not any(call[0] == "handle" for call in service.calls)
    assert not gateway.sent


def test_result_chunks_plain_text_no_mentions():
    service, gateway, transport, event = setup(channel=30)
    service.session["thread_id"] = "30"
    asyncio.run(transport.command(event, "query", text="조회"))
    assert len(gateway.sent) > 1
    assert all(len(text) <= 1900 and "@everyone" not in text and "**결과**" not in text for _, text in gateway.sent)


def test_deleted_thread_recovery_preserves_session():
    service, gateway, transport, event = setup()
    service.session["thread_id"] = "99"
    gateway.missing = True
    asyncio.run(transport.command(event, "resume", session_id="s1"))
    assert service.session["session_id"] == "s1"
    assert service.session["thread_id"] == "30"
    assert not any(call[0] == "start" for call in service.calls)


def test_resume_no_session_displays_service_guidance():
    service, gateway, transport, event = setup()
    service.resume = lambda *args: {"messages": ["재개할 과제가 없습니다."]}
    asyncio.run(transport.command(event, "resume"))
    assert gateway.replies == ["재개할 과제가 없습니다."]
    assert not gateway.sent and "create" not in gateway.events


def test_permission_failure_preserves_existing_binding():
    service, gateway, transport, event = setup()
    service.session["thread_id"] = "30"
    gateway.denied = True
    asyncio.run(transport.command(event, "resume", session_id="s1"))
    assert service.session["thread_id"] == "30"
    assert "create" not in gateway.events


@pytest.mark.parametrize("owner,private,bot", [(2, True, False), (1, False, False), (1, True, True)])
def test_unrelated_natural_message_is_silent(owner, private, bot):
    service, gateway, transport, event = setup(owner, 30)
    service.session["thread_id"] = "30"
    event.channel.private = private
    event.user.bot = bot
    message = NS(id=101, author=event.user, guild=event.guild, channel=event.channel, content="조회")
    asyncio.run(transport.message(message))
    assert not any(call[0] == "handle" for call in service.calls)
    assert not gateway.sent


def test_owner_reply_uses_service_answer_action():
    service, gateway, transport, event = setup(channel=30)
    service.session["thread_id"] = "30"
    message = NS(id=101, author=event.user, guild=event.guild, channel=event.channel,
                 content="가설을 수정할게요", reference=NS(message_id=99))
    asyncio.run(transport.message(message))
    call = next(call for call in service.calls if call[0] == "handle")
    assert call[1] == ("1", "s1", "101", "answer")
    assert call[2]["text"] == message.content
    assert call[2]['payload']['reply_to_message_id'] == '99'


def test_unreadable_owner_message_gets_guidance_without_model_call():
    service, gateway, transport, event = setup(channel=30)
    service.session['thread_id'] = '30'
    gateway.message_content_enabled = False
    message = NS(reference=NS(message_id=101), id=102, author=event.user, guild=event.guild, channel=event.channel, content='', attachments=[])
    asyncio.run(transport.message(message))
    assert '@\u200bDA-Agent' in gateway.sent[-1][1]
    assert not any(call[0] == 'handle' for call in service.calls)


@pytest.mark.parametrize('state,expected', [('error', '조회하지 못했습니다'), ('clarification', '조건을 확인 중'), ('success', '결과를 저장')])
def test_query_acknowledgement_matches_actual_state(state, expected):
    service, gateway, transport, event = setup(channel=30)
    service.session['thread_id'] = '30'
    service.handle = lambda *args, **kwargs: {'messages': ['내용'], 'request_state': state}
    asyncio.run(transport.command(event, 'query', text='조회'))
    assert expected in gateway.replies[-1]


def test_internal_errors_never_leak_secrets():
    service, gateway, transport, event = setup(channel=30)
    service.session["thread_id"] = "30"
    def failed(*args, **kwargs):
        raise RuntimeError("postgresql://secret:password@host")
    service.handle = failed
    asyncio.run(transport.command(event, "query", text="조회"))
    assert all("password" not in text for text in gateway.replies)


def test_settings_require_explicit_isolated_configuration(tmp_path):
    key = tmp_path / "key"
    key.write_text("not-a-real-key")
    env = {"DISCORD_BOT_TOKEN": "not-a-real-token", "DISCORD_GUILD_IDS": "10", "DISCORD_RECORDS_DSN": "postgresql://u:p@localhost/discord_records",
           "DISCORD_ADMIN_DSN": "postgresql://a:p@localhost/discord_training", "DISCORD_LEARNER_DSN": "postgresql://l:p@localhost/discord_training",
           "DISCORD_GEMINI_KEY_FILE": str(key)}
    settings = DiscordSettings.from_env(env)
    assert settings.guild_ids == (10,) and not settings.message_content
    assert "not-a-real-token" not in repr(settings)
    with pytest.raises(ValueError):
        DiscordSettings.from_env({})
    env["DISCORD_RECORDS_DSN"] = "postgresql://u:p@localhost/records"
    with pytest.raises(ValueError, match="isolated"):
        DiscordSettings.from_env(env)


def test_optional_real_command_registration_without_login(tmp_path):
    pytest.importorskip("discord")
    settings = DiscordSettings("fake", (10,), "r", "a", "l", tmp_path / "key")
    async def check():
        client = create_client(Service(), settings)
        assert {cmd.name for cmd in client.da_command_tree.get_commands()} == {"training", "resume", "query", "answer", "question", "help", "report", "followup", "submit", "sql", "evidence", "end", "tip", "history", "retry"}
        assert not client.intents.message_content
        parameters=client.da_command_tree.get_command('training').parameters
        text=next(p for p in parameters if p.name=='text')
        assert text.required and not text.choices
        assert 'topic' not in {p.name for p in parameters}
        await client.close()
    asyncio.run(check())


def test_main_reaches_sdk_start_without_invalid_logging_configuration(tmp_path, monkeypatch):
    pytest.importorskip("discord")
    from da_agent import discord_bot, discord_provider, discord_store
    settings = DiscordSettings("fake", (10,), "r", "a", "l", tmp_path / "key")
    monkeypatch.setattr(DiscordSettings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(discord_provider, "DiscordGemmaProvider", lambda **kwargs: NS())
    monkeypatch.setattr(discord_store, "DiscordStore", lambda dsn: NS(initialize=lambda: None))
    class StartupReached(Exception):
        pass
    def client_factory(service, configured):
        client = create_client(service, configured)
        async def start(token, *, reconnect=True):
            assert token == "fake"
            raise StartupReached()
        client.start = start
        return client
    monkeypatch.setattr(discord_bot, "create_client", client_factory)
    with pytest.raises(StartupReached):
        discord_bot.main()


@pytest.mark.parametrize("privileged", [False, True])
@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("access_denied", [False, True])
def test_sdk_gateway_rejects_additional_ordinary_member(tmp_path, monkeypatch, privileged, archived, access_denied):
    discord = pytest.importorskip("discord")
    permissions = NS(view_channel=True, create_private_threads=True, send_messages_in_threads=True,
                     manage_threads=True, read_message_history=True, administrator=False)
    extra = NS(id=2)
    owner = NS(id=1)
    guild = NS(id=10, me=NS(id=99), get_member=lambda member_id: extra)
    class Parent:
        def __init__(self):
            self.guild = guild
        def permissions_for(self, member):
            return NS(**{**vars(permissions), "manage_threads": privileged}) if member.id == 2 else permissions
    class Thread:
        def __init__(self):
            self.guild, self.parent, self.invitable, self.archived = guild, Parent(), False, archived
            self.joined = False
        def is_private(self):
            return True
        async def join(self):
            assert not self.archived
            self.joined = True
        async def edit(self, **kwargs):
            self.archived = kwargs['archived']
        async def fetch_members(self):
            assert self.joined and not self.archived
            if access_denied:
                raise discord.Forbidden(NS(status=403, reason='Forbidden'), {'code': 50001, 'message': 'Missing Access'})
            return [NS(id=1), NS(id=2), NS(id=99)]
    monkeypatch.setattr(discord, "Thread", Thread)
    monkeypatch.setattr(discord, "TextChannel", Parent)
    async def check():
        client = create_client(Service(), DiscordSettings("fake", (10,), "r", "a", "l", tmp_path / "key"))
        client._connection.user = NS(id=99)
        if access_denied:
            with pytest.raises(DomainError, match="Server Members Intent"):
                await client.da_transport.gateway.validate_thread(Thread(), owner)
        elif privileged:
            await client.da_transport.gateway.validate_thread(Thread(), owner)
        else:
            with pytest.raises(DomainError, match="다른 일반 참가자"):
                await client.da_transport.gateway.validate_thread(Thread(), owner)
        await client.close()
    asyncio.run(check())
