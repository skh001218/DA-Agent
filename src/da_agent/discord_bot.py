"""Optional standalone bot. Importing the web app never imports this module."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from .discord_transport import DiscordTransport
from .errors import DomainError


@dataclass(frozen=True)
class DiscordSettings:
    token: str = field(repr=False)
    guild_ids: tuple[int, ...]
    records_dsn: str = field(repr=False)
    admin_dsn: str = field(repr=False)
    learner_dsn: str = field(repr=False)
    gemini_key_file: Path
    message_content: bool = False
    query_timeout_ms: int = 5000
    max_rows: int = 1000
    max_bytes: int = 1048576
    preview_rows: int = 200
    execution_ttl: int = 600
    daily_call_limit: int = 30

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        names = ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_IDS", "DISCORD_RECORDS_DSN",
                 "DISCORD_ADMIN_DSN", "DISCORD_LEARNER_DSN", "DISCORD_GEMINI_KEY_FILE")
        if any(not env.get(name, "").strip() for name in names):
            raise ValueError("Discord 설정 필수: " + ", ".join(names))
        guilds = tuple(int(value.strip()) for value in env[names[1]].split(","))
        if not guilds or any(value <= 0 for value in guilds):
            raise ValueError("DISCORD_GUILD_IDS must contain positive server IDs")
        from urllib.parse import urlparse
        dsns = [env[name] for name in names[2:5]]
        databases = [urlparse(value).path.strip("/") for value in dsns]
        if any(urlparse(value).scheme not in {"postgresql", "postgres"} for value in dsns):
            raise ValueError("Discord DSNs must be PostgreSQL URLs")
        if any(not name or name in {"records", "training", "postgres"} for name in databases):
            raise ValueError("Discord DBs must be explicitly isolated from web records/training")
        if databases[0] == databases[1] or databases[0] == databases[2]:
            raise ValueError("Discord records DB must be separate from the dataset DB")
        key_file = Path(env[names[5]])
        if not key_file.is_file():
            raise ValueError("DISCORD_GEMINI_KEY_FILE does not exist")
        daily_limit = int(env.get("DISCORD_DAILY_CALL_LIMIT", "30"))
        if daily_limit <= 0:
            raise ValueError("DISCORD_DAILY_CALL_LIMIT must be positive")
        return cls(env[names[0]], guilds, *dsns, key_file,
                   message_content=env.get("DISCORD_MESSAGE_CONTENT", "false").lower() == "true",
                   daily_call_limit=daily_limit)


def create_client(service, settings):
    try:
        import discord
        from discord import app_commands
    except ImportError as exc:
        raise RuntimeError("Install requirements-discord.txt for the standalone bot") from exc

    class Gateway:
        def __init__(self, client):
            self.client = client

        async def defer(self, event):
            await event.response.defer(ephemeral=True, thinking=True)

        async def reply(self, event, text):
            if event.is_expired():
                return  # permanent thread response already delivered; /resume restores state
            await event.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        async def send(self, channel, text):
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())

        def is_private_thread(self, channel):
            return isinstance(channel, discord.Thread) and channel.is_private()

        async def validate_parent(self, channel, user):
            if not isinstance(channel, discord.TextChannel):
                raise DomainError("discord_parent_required", "시작·복구는 허용된 서버의 텍스트 채널에서 사용하세요.")
            bot = channel.permissions_for(channel.guild.me)
            owner = channel.permissions_for(user)
            if not all(getattr(bot, name, False) for name in ("view_channel", "create_private_threads", "send_messages_in_threads", "manage_threads", "read_message_history")):
                raise DomainError("discord_permissions", "봇의 비공개 스레드 생성·관리·발언·기록 읽기 권한을 확인하세요.", 403)
            if not owner.view_channel or not owner.send_messages_in_threads:
                raise DomainError("discord_owner_permissions", "부모 채널 보기·스레드 발언 권한이 필요합니다.", 403)

        async def validate_thread(self, channel, user):
            if not self.is_private_thread(channel) or channel.invitable:
                raise DomainError("discord_private_required", "비공개·초대 불가 스레드가 필요합니다.", 403)
            if str(channel.guild.id) not in transport.guild_ids:
                raise DomainError("discord_guild_forbidden", "허용되지 않은 서버입니다.", 403)
            await self.validate_parent(channel.parent, user)
            members = await channel.fetch_members()
            if not any(member.id == user.id for member in members):
                raise DomainError("discord_membership", "과제 스레드 참가 상태를 확인하세요.", 403)
            for member in members:
                if member.id in {user.id, self.client.user.id}:
                    continue
                participant = channel.guild.get_member(member.id) or await channel.guild.fetch_member(member.id)
                permissions = channel.parent.permissions_for(participant)
                if not permissions.manage_threads and not permissions.administrator:
                    raise DomainError("discord_extra_member", "과제 스레드에 다른 일반 참가자가 있습니다. 운영자가 해당 참가자를 제거한 뒤 /resume 하세요.", 403)
            if channel.archived:
                await channel.edit(archived=False, locked=False, invitable=False)

        async def create_private_thread(self, parent, user, session_id):
            thread = await parent.create_thread(name=f"DA-{session_id[:12]}", type=discord.ChannelType.private_thread,
                                                invitable=False, auto_archive_duration=1440)
            try:
                await thread.add_user(user)
                await self.validate_thread(thread, user)
            except Exception:
                await thread.edit(archived=True, locked=True)
                raise
            return thread

        async def fetch_channel(self, channel_id):
            try:
                return await self.client.fetch_channel(channel_id)
            except discord.NotFound:
                return None
            except discord.Forbidden:
                raise DomainError("discord_access_denied", "원래 과제 스레드에 접근할 수 없습니다. 운영자에게 부모 채널·스레드 권한과 참가 상태 복구를 요청한 뒤 부모 채널에서 /resume 하세요. 삭제된 스레드는 새 비공개 공간으로 복구됩니다.", 403)

    class Client(discord.Client):
        async def setup_hook(self):
            for guild_id in settings.guild_ids:
                guild = discord.Object(id=guild_id)
                tree.copy_global_to(guild=guild)
                await tree.sync(guild=guild)

        async def on_message(self, message):
            if settings.message_content:
                await transport.message(message)

    intents = discord.Intents.default()
    intents.message_content = settings.message_content
    client = Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())
    tree = app_commands.CommandTree(client)
    transport = DiscordTransport(service, Gateway(client), settings.guild_ids)

    @tree.command(name="training", description="비공개 분석 훈련 시작")
    @app_commands.guild_only()
    @app_commands.choices(difficulty=[app_commands.Choice(name="초급", value="beginner"), app_commands.Choice(name="중급", value="intermediate"), app_commands.Choice(name="고급", value="advanced")])
    @app_commands.choices(help_level=[app_commands.Choice(name="안내 포함", value="guided"), app_commands.Choice(name="내 정의 먼저", value="independent")])
    async def training(interaction: discord.Interaction, topic: str = "tutorial", difficulty: str = "intermediate", help_level: str | None = None):
        await transport.command(interaction, "training", topic=topic, difficulty=difficulty, help_level=help_level)

    @tree.command(name="resume", description="자신의 과제를 재개하거나 삭제된 스레드를 복구")
    @app_commands.guild_only()
    async def resume(interaction: discord.Interaction, session_id: str | None = None):
        await transport.command(interaction, "resume", session_id=session_id)

    # Slash alternatives work without the privileged Message Content Intent.
    def register_text_action(action, description):
        async def callback(interaction: discord.Interaction, text: str):
            await transport.command(interaction, action, text=text)
        callback.__annotations__["interaction"] = discord.Interaction
        tree.add_command(app_commands.Command(name=action, description=description, callback=callback))

    for action, description in {"query": "조회 요청 또는 확인 질문 답변", "followup": "업무 담당자 후속 질문에 답변"}.items():
        register_text_action(action, description)

    @tree.command(name="report", description="보고 초안 작성·수정 또는 긴 보고 이어 쓰기")
    async def report(interaction: discord.Interaction, text: str, append: bool = False):
        await transport.command(interaction, 'report', text=text, payload={'append': append})

    @tree.command(name="help", description="개념·분석 방향·중간 피드백 도움 요청")
    @app_commands.choices(kind=[app_commands.Choice(name='개념', value='concept_hint'), app_commands.Choice(name='분석 방향', value='analysis_direction_hint'), app_commands.Choice(name='중간 검토', value='intermediate_feedback')])
    async def help_command(interaction: discord.Interaction, text: str, kind: str = 'concept_hint'):
        await transport.command(interaction, 'help', text=text, payload={'help_type': kind})

    def register_action(action, description):
        async def callback(interaction: discord.Interaction):
            await transport.command(interaction, action)
        callback.__annotations__["interaction"] = discord.Interaction
        tree.add_command(app_commands.Command(name=action, description=description, callback=callback))

    for action, description in {"submit": "최종 보고 제출과 평가", "end": "훈련 중단·기록 보존"}.items():
        register_action(action, description)

    def register_execution(action, description):
        async def callback(interaction: discord.Interaction, execution_id: str):
            await transport.command(interaction, action, payload={"execution_id": execution_id})
        callback.__annotations__["interaction"] = discord.Interaction
        tree.add_command(app_commands.Command(name=action, description=description, callback=callback))

    register_execution("sql", "저장된 조회의 실제 실행 SQL 보기")
    register_execution("evidence", "성공 조회를 보고 근거로 선택")
    client.da_transport = transport
    client.da_command_tree = tree
    return client


def main():
    settings = DiscordSettings.from_env()
    from .api_provider import GeminiProvider
    from .discord_store import DiscordStore
    from .discord_service import DiscordTrainingService
    from .discord_query import DiscordQueryEngine
    from .sql_runner import SqlRunner
    provider = GeminiProvider(key_file=settings.gemini_key_file)
    store = DiscordStore(settings.records_dsn)
    store.initialize()
    query_engine = DiscordQueryEngine(provider, SqlRunner(settings), settings)
    service = DiscordTrainingService(store, query_engine, provider, settings)
    client = create_client(service, settings)
    client.run(settings.token, log_level=None)


if __name__ == "__main__":
    main()
