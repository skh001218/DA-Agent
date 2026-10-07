"""Optional standalone bot. Importing the web app never imports this module."""
import os
import re
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
    gemini_key_file: Path | None
    message_content: bool = False
    query_timeout_ms: int = 5000
    max_rows: int = 1000
    max_bytes: int = 1048576
    preview_rows: int = 200
    execution_ttl: int = 600
    daily_call_limit: int = 30
    llm_model: str = 'gemma-4-26b-a4b-it'
    quality_profiles_directory: str = '.local/evaluation-quality'
    generation_directory: str = '.local/discord-generation'
    llm_provider: str = 'gemma'
    codex_bin: str = 'codex'
    codex_home: str | None = None
    codex_model: str | None = None
    codex_timeout_seconds: int = 180

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        provider = env.get('DISCORD_LLM_PROVIDER', 'gemma')
        if provider not in {'gemma', 'codex_cli'}:
            raise ValueError('DISCORD_LLM_PROVIDER must be gemma or codex_cli')
        names = ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_IDS", "DISCORD_RECORDS_DSN",
                 "DISCORD_ADMIN_DSN", "DISCORD_LEARNER_DSN")
        if provider == 'gemma':
            names += ("DISCORD_GEMINI_KEY_FILE",)
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
        key_file = Path(env['DISCORD_GEMINI_KEY_FILE']) if provider == 'gemma' else None
        if key_file is not None and not key_file.is_file():
            raise ValueError("DISCORD_GEMINI_KEY_FILE does not exist")
        daily_limit = int(env.get("DISCORD_DAILY_CALL_LIMIT", "30"))
        if daily_limit < 0:
            raise ValueError("DISCORD_DAILY_CALL_LIMIT must be non-negative (0 means unlimited)")
        codex_model = env.get('DISCORD_CODEX_MODEL', '').strip() or None
        timeout = int(env.get('DISCORD_CODEX_TIMEOUT_SECONDS', '180'))
        model = env.get('DISCORD_MODEL', 'gemma-4-26b-a4b-it') if provider == 'gemma' else codex_model or 'codex-default'
        if provider == 'gemma' and not re.fullmatch(r'gemma-[a-zA-Z0-9._-]{1,95}', model):
            raise ValueError('DISCORD_MODEL must be a Gemma model ID')
        if provider == 'codex_cli':
            from .codex_provider import MODEL_PATTERN
            if codex_model and not re.fullmatch(MODEL_PATTERN, codex_model):
                raise ValueError('Invalid DISCORD_CODEX_MODEL')
            if not 1 <= timeout <= 600:
                raise ValueError('DISCORD_CODEX_TIMEOUT_SECONDS must be between 1 and 600')
        return cls(env[names[0]], guilds, *dsns, key_file,
                   message_content=env.get("DISCORD_MESSAGE_CONTENT", "false").lower() == "true",
                   daily_call_limit=daily_limit, llm_model=model,
                   quality_profiles_directory=env.get('DISCORD_QUALITY_PROFILES_DIR','.local/evaluation-quality'),
                   generation_directory=env.get('DISCORD_GENERATION_DIRECTORY','.local/discord-generation'),
                   llm_provider=provider, codex_bin=env.get('DISCORD_CODEX_BIN', 'codex'),
                   codex_home=env.get('DISCORD_CODEX_HOME') or None, codex_model=codex_model,
                   codex_timeout_seconds=timeout)


def create_client(service, settings):
    try:
        import discord
        from discord import app_commands
    except ImportError as exc:
        raise RuntimeError("Install requirements-discord.txt for the standalone bot") from exc

    class Gateway:
        def __init__(self, client):
            self.client = client
            from .discord_responses import DiscordResponses
            self.responses = DiscordResponses(os.environ.get('DISCORD_RESPONSE_DIRECTORY', '.local/discord-responses'))

        async def defer(self, event):
            await self.responses.defer(event)

        async def reply(self, event, text):
            await self.responses.reply(event, text, discord.AllowedMentions.none())

        async def interrupt(self, event):
            await self.responses.interrupt(event, discord.AllowedMentions.none())

        def finish(self, event):
            self.responses.finish(event.id)

        async def send(self, channel, text):
            return await channel.send(text, allowed_mentions=discord.AllowedMentions.none())

        async def generation_controls(self,channel,session):
            if session.get('generation',{}).get('status') not in {'accepted','failed','interrupted'}: return
            if session['generation'].get('error_code') in {'api_input_budget','planning_limit','api_key_missing','api_key_invalid','model_unavailable','codex_cli_unavailable','codex_login_required','codex_request_invalid'}: return
            view=discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label='출제 재시도',custom_id='generation-retry:'+session['session_id']))
            await channel.send('저장된 요청으로 수동 재시도합니다. 남은 모델 호출 한도가 적용됩니다.',view=view)

        async def rename_generated_thread(self,channel,user,session):
            await self.validate_thread(channel,user)
            named=await transport._call('reserve_thread_name',str(user.id),session['session_id'])
            if channel.name!=named['thread_name']: await channel.edit(name=named['thread_name'])

        async def send_table(self, channel, table):
            import asyncio
            from io import BytesIO
            from .discord_tables import render_table_png, table_fallback
            pages = await asyncio.to_thread(render_table_png, table)
            message_ids = []
            for index, page in enumerate(pages, 1):
                attachment = discord.File(BytesIO(page), filename=f'table-{index}.png',
                                          description=table_fallback(table)[:1024])
                sent = await channel.send(file=attachment, allowed_mentions=discord.AllowedMentions.none())
                message_ids.append(str(sent.id))
            return message_ids

        @property
        def bot_user_id(self):
            return self.client.user.id if self.client.user else None

        @property
        def message_content_enabled(self):
            return settings.message_content

        def message_text(self, message):
            text = message.content
            if self.bot_user_id:
                text = re.sub(r'<@!?' + str(self.bot_user_id) + r'>', '', text)
            return text.strip()

        async def publish_result(self, parent, user, submission, publication, save):
            from .discord_forum import ResultForumPublisher
            return await ResultForumPublisher(self.client).publish(parent, user, submission, publication, save)

        async def send_result_card(self, channel, submission, index, user):
            from .discord_forum import ResultForumPublisher
            await channel.send(embed=ResultForumPublisher(self.client).embed(submission, index, user),
                               allowed_mentions=discord.AllowedMentions.none())

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

        async def validate_thread(self, channel, user, *, reopen=True):
            if not self.is_private_thread(channel) or channel.invitable:
                raise DomainError("discord_private_required", "비공개·초대 불가 스레드가 필요합니다.", 403)
            if str(channel.guild.id) not in transport.guild_ids:
                raise DomainError("discord_guild_forbidden", "허용되지 않은 서버입니다.", 403)
            await self.validate_parent(channel.parent, user)
            # The creator is not necessarily a member of a newly created private
            # thread. Restore/join before the members endpoint, which otherwise
            # returns Missing Access even for the creating bot.
            if channel.archived and reopen:
                await channel.edit(archived=False, locked=False, invitable=False)
            if reopen:
                await channel.join()
            try:
                members = await channel.fetch_members()
            except discord.Forbidden:
                raise DomainError('discord_members_access', '스레드 참가자 조회가 차단됐습니다. 운영자가 Discord Developer Portal → Bot → Server Members Intent를 켜고 채널 접근 권한을 확인한 뒤 /resume 하세요.', 403) from None
            if not any(member.id == user.id for member in members):
                raise DomainError("discord_membership", "과제 스레드 참가 상태를 확인하세요.", 403)
            for member in members:
                if member.id in {user.id, self.client.user.id}:
                    continue
                participant = channel.guild.get_member(member.id) or await channel.guild.fetch_member(member.id)
                permissions = channel.parent.permissions_for(participant)
                if not permissions.manage_threads and not permissions.administrator:
                    raise DomainError("discord_extra_member", "과제 스레드에 다른 일반 참가자가 있습니다. 운영자가 해당 참가자를 제거한 뒤 /resume 하세요.", 403)

        async def create_private_thread(self, parent, user, session_id, *, name):
            thread = await parent.create_thread(name=name, type=discord.ChannelType.private_thread,
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

        async def archive_task_thread(self, channel, user):
            await self.validate_thread(channel, user, reopen=False)
            if not channel.archived or not channel.locked:
                await channel.edit(archived=True, locked=True, invitable=False,
                                   reason='최종 보고서·평가 게시 완료 후 과제 기록 보관')

    class Client(discord.Client):
        async def setup_hook(self):
            from .discord_pdf_view import ResultPDFView
            self.add_view(ResultPDFView(settings.guild_ids))
            import asyncio
            await transport.gateway.responses.recover()
            self.response_maintenance = asyncio.create_task(transport.gateway.responses.maintenance())
            for guild_id in settings.guild_ids:
                guild = discord.Object(id=guild_id)
                tree.copy_global_to(guild=guild)
                await tree.sync(guild=guild)

        async def close(self):
            import asyncio
            maintenance = getattr(self, 'response_maintenance', None)
            if maintenance is not None:
                maintenance.cancel()
                await asyncio.gather(maintenance, return_exceptions=True)
            try:
                await transport.gateway.responses.recover(include_active=True)
            finally:
                await super().close()

        async def on_message(self, message):
            await transport.message(message)

        async def on_interaction(self,interaction):
            custom_id=(interaction.data or {}).get('custom_id','')
            if custom_id.startswith('generation-retry:'):
                await transport.command(interaction,'retry',session_id=custom_id.split(':',1)[1])

    intents = discord.Intents.default()
    intents.message_content = settings.message_content
    client = Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())
    tree = app_commands.CommandTree(client)
    transport = DiscordTransport(service, Gateway(client), settings.guild_ids)

    @tree.command(name="training", description="SQL 또는 분석 연습을 선택해 비공개 훈련 시작")
    @app_commands.guild_only()
    @app_commands.choices(practice=[app_commands.Choice(name="SQL 연습", value="sql"), app_commands.Choice(name="분석 연습", value="analysis")])
    @app_commands.describe(text='연습할 분석 내용과 목표를 입력하세요 (1~4000자)')
    @app_commands.choices(difficulty=[app_commands.Choice(name="초급", value="beginner"), app_commands.Choice(name="중급", value="intermediate"), app_commands.Choice(name="고급", value="advanced")])
    @app_commands.choices(help_level=[app_commands.Choice(name="안내 포함", value="guided"), app_commands.Choice(name="내 정의 먼저", value="independent")])
    async def training(interaction: discord.Interaction, text: str, practice: str, difficulty: str = "intermediate", help_level: str | None = None, source_session_id: str | None = None):
        await transport.command(interaction, "training", text=text, difficulty=difficulty, help_level=help_level, practice=practice, source_session_id=source_session_id)

    @tree.command(name="tip", description="명령어의 사용법과 예시 확인")
    @app_commands.guild_only()
    @app_commands.describe(command="확인할 명령어 이름 (예: report 또는 /report). 생략하면 목록")
    async def tip(interaction: discord.Interaction, command: str = ''):
        await transport.command(interaction, "tip", text=command)

    @tree.command(name="resume", description="진행 과제 재개 또는 완료 과제 결과·대화 열람")
    @app_commands.guild_only()
    async def resume(interaction: discord.Interaction, session_id: str | None = None):
        await transport.command(interaction, "resume", session_id=session_id)

    @tree.command(name="history", description="본인의 분석 연습 기록과 결과 링크 확인")
    @app_commands.guild_only()
    async def history(interaction: discord.Interaction, page: app_commands.Range[int, 1, 1000000] = 1):
        await transport.command(interaction, 'history', payload={'page': page})

    # Slash alternatives work without the privileged Message Content Intent.
    def register_text_action(action, description):
        async def callback(interaction: discord.Interaction, text: str):
            await transport.command(interaction, action, text=text)
        callback.__annotations__["interaction"] = discord.Interaction
        tree.add_command(app_commands.Command(name=action, description=description, callback=callback))

    for action, description in {"sqlrun": "SQL 연습의 sql 코드 블록을 직접 실행", "query": "새로운 자연어 조회 요청", "answer": "현재 봇 질문에 이어서 답변", "question": "게임 분석 용어를 용어당 최대 3줄로 설명", "followup": "업무 담당자 후속 질문에 답변"}.items():
        register_text_action(action, description)

    @tree.command(name="report", description="보고 초안 작성·수정 또는 긴 보고 이어 쓰기")
    async def report(interaction: discord.Interaction, text: str, append: bool = False):
        await transport.command(interaction, 'report', text=text, payload={'append': append})

    @tree.command(name="help", description="개념·분석 방향·중간 피드백 도움 요청")
    @app_commands.choices(kind=[app_commands.Choice(name='문제 원문', value='task_details'), app_commands.Choice(name='개념', value='concept_hint'), app_commands.Choice(name='분석 방향', value='analysis_direction_hint'), app_commands.Choice(name='중간 검토', value='intermediate_feedback'), app_commands.Choice(name='데이터 사전', value='data_dictionary'), app_commands.Choice(name='평가 기준', value='evaluation_criteria'), app_commands.Choice(name='전체 명령', value='commands'), app_commands.Choice(name='SQL 해설 공개', value='solution')])
    async def help_command(interaction: discord.Interaction, text: str = '', kind: str = 'concept_hint'):
        await transport.command(interaction, 'help', text=text, payload={'help_type': kind})

    def register_action(action, description):
        async def callback(interaction: discord.Interaction):
            await transport.command(interaction, action)
        callback.__annotations__["interaction"] = discord.Interaction
        tree.add_command(app_commands.Command(name=action, description=description, callback=callback))

    @tree.command(name="submit", description="분석 보고 또는 SQL 풀이의 최종 평가")
    async def submit(interaction: discord.Interaction, execution_id: str | None = None):
        await transport.command(interaction, 'submit', payload={'execution_id': execution_id} if execution_id else {})

    for action, description in {"end": "훈련 중단·기록 보존", "retry":"실패·중단된 출제 수동 재시도"}.items():
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
    from .discord_provider import configured_discord_provider
    from .discord_store import DiscordStore
    from .discord_service import DiscordTrainingService
    from .discord_query import DiscordQueryEngine
    from .sql_runner import SqlRunner
    provider = configured_discord_provider(settings)
    store = DiscordStore(settings.records_dsn)
    store.initialize()
    query_engine = DiscordQueryEngine(provider, SqlRunner(settings), settings)
    service = DiscordTrainingService(store, query_engine, provider, settings)
    client = create_client(service, settings)
    client.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
