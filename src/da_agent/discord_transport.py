"""Discord-independent command orchestration; gateway supplies async Discord I/O."""
import asyncio
import re
from weakref import WeakValueDictionary

from .errors import DomainError


def safe_chunks(text, limit=1900):
    """Treat service/model/user output as plain text, never mentions or markup."""
    text = str(text).replace("@", "@\u200b")
    text = re.sub(r"([\\`*_{}\[\]()<>|~#])", r"\\\1", text)
    if not text:
        return ['(내용 없음)']
    if limit < 2:
        raise ValueError('Message limit must be at least 2')
    chunks = []
    while len(text) > limit:
        # Keep paragraphs, then lines, then words together whenever possible.
        cut = -1
        for separator in ('\n\n', '\n', ' '):
            position = text.rfind(separator, 0, limit + 1)
            if position > 0:
                cut = position
                break
        if cut < 1:
            cut = limit
        # Never split a Markdown escape pair across messages.
        if (len(text[:cut]) - len(text[:cut].rstrip('\\'))) & 1:
            cut -= 1
        chunks.append(text[:cut])
        text = text[cut:]
    chunks.append(text)
    return chunks or ["(내용 없음)"]


class DiscordTransport:
    ACTIONS = {"query", "help", "report", "followup", "submit", "sql", "evidence", "end", "message"}

    def __init__(self, service, gateway, guild_ids):
        self.service, self.gateway = service, gateway
        self.guild_ids = {str(value) for value in guild_ids}
        self._thread_locks = WeakValueDictionary()
        self._result_locks = WeakValueDictionary()
        if not self.guild_ids:
            raise ValueError("Discord guild allowlist is required")

    async def _call(self, name, *args, **kwargs):
        return await asyncio.to_thread(getattr(self.service, name), *args, **kwargs)

    def _guild(self, event):
        if not event.guild or str(event.guild.id) not in self.guild_ids:
            raise DomainError("discord_guild_forbidden", "허용된 서버에서만 사용할 수 있습니다.", 403)

    async def _emit(self, channel, messages, tables=()):
        from .discord_tables import table_fallback
        for index, message in enumerate(messages):
            for chunk in safe_chunks(message):
                await self.gateway.send(channel, chunk)
            for table in tables:
                if table.get('after_message', 0) != index:
                    continue
                try:
                    await self.gateway.send_table(channel, table)
                except Exception:
                    # A display failure must not invalidate a saved successful query.
                    for chunk in safe_chunks('표 첨부를 표시하지 못해 행별 목록으로 제공합니다.\n' + table_fallback(table)):
                        await self.gateway.send(channel, chunk)

    async def _session(self, event, session_id=None):
        owner = str(event.user.id)
        if session_id:
            session = await self._call("get_session", owner, session_id)
        else:
            sessions = await self._call("list_sessions", owner, str(event.guild.id))
            session = next((s for s in sessions if str(s.get("thread_id")) == str(event.channel.id)), None)
        if not session or str(session.get("owner_user_id")) != owner or str(session.get("guild_id")) != str(event.guild.id):
            raise DomainError("discord_session_forbidden", "자신의 과제 스레드에서 명령을 사용하세요.", 403)
        if str(session.get("thread_id")) != str(event.channel.id):
            raise DomainError("discord_wrong_thread", "이 과제의 전용 스레드에서 명령을 사용하세요.", 403)
        await self.gateway.validate_thread(event.channel, event.user)
        return session

    async def command(self, event, action, *, session_id=None, text="", payload=None,
                      topic="tutorial", difficulty="intermediate", help_level=None):
        # First operation is the acknowledgement; model/DB work never precedes it.
        await self.gateway.defer(event)
        try:
            self._guild(event)
            owner, guild, event_id = str(event.user.id), str(event.guild.id), str(event.id)
            if action == "training":
                await self.gateway.validate_parent(event.channel, event.user)
                session = await self._call("start", owner, guild, str(event.channel.id), event_id,
                                           topic=topic, difficulty=difficulty, help_level=help_level)
                if not session.get("session_id"):
                    for message in session.get("messages", ["과제 준비에 실패했습니다. 다시 시작하세요."]):
                        for chunk in safe_chunks(message):
                            await self.gateway.reply(event, chunk)
                    return
                channel = await self._thread(event, session)
                response = await self._call("resume", owner, guild, session["session_id"])
                await self._emit(channel, response.get("messages", []), response.get("tables", []))
                await self.gateway.reply(event, f"과제 공간: <#{channel.id}>. 서버 관리자와 스레드 관리 권한자는 접근할 수 있습니다.")
                return
            if action == "resume":
                response = await self._call("resume", owner, guild, session_id)
                if not response.get("session"):
                    for message in response.get("messages", ["재개할 과제가 없습니다. 부모 텍스트 채널에서 /training 으로 시작하세요."]):
                        for chunk in safe_chunks(message):
                            await self.gateway.reply(event, chunk)
                    return
                session = response["session"]
                channel = await self._thread(event, session)
                if session.get("state") in {"stopped", "interrupted"}:
                    await self._call("handle", owner, session["session_id"], event_id, "continue")
                    response = await self._call("resume", owner, guild, session["session_id"])
                await self._emit(channel, response.get("messages", []), response.get("tables", []))
                if response.get('submission'):
                    await self._publish_result(event, channel, response['submission'])
                await self.gateway.reply(event, f"재개 공간: <#{channel.id}>")
                return
            if action not in self.ACTIONS:
                raise DomainError("discord_unknown_action", "지원하지 않는 명령입니다.")
            session = await self._session(event, session_id)
            response = await self._call("handle", owner, session["session_id"], event_id, action, text=text, payload=payload)
            await self._emit(event.channel, response.get("messages", []), response.get("tables", []))
            if response.get('submission'):
                await self._publish_result(event, event.channel, response['submission'])
                await self.gateway.reply(event, '제출 결과 안내를 과제 스레드에 남겼습니다.')
                return
            acknowledgement = {'clarification': '조회에 필요한 조건을 확인 중입니다. 스레드의 질문에 답해주세요.',
                               'error': '조회하지 못했습니다. 스레드의 오류 안내를 확인하세요.',
                               'success': '조회 결과를 저장했습니다. 스레드에서 확인하세요.'}
            await self.gateway.reply(event, acknowledgement.get(response.get('request_state'), '응답을 스레드에 남겼습니다. 내용을 확인하세요.'))
        except Exception as exc:
            # Do not expose exception strings: HTTP/DB errors can include credentials.
            message = exc.message if isinstance(exc, DomainError) else "처리하지 못했습니다. 기록은 보존됩니다. 권한·연결 상태를 확인한 뒤 /resume 으로 재개하세요."
            await self.gateway.reply(event, safe_chunks(message)[0])

    async def _publish_result(self, event, channel, submission):
        owner, sid, eid = str(event.user.id), submission['session_id'], submission['evaluation_id']
        if submission['owner_user_id'] != owner or submission['guild_id'] != str(event.guild.id):
            raise DomainError('result_owner', '자신의 서버·제출 결과만 게시할 수 있습니다.', 403)
        lock = self._result_locks.setdefault(str(event.guild.id), asyncio.Lock())
        async with lock:
            publication = await self._call('result_publication', owner, sid, eid)
            async def save(**changes):
                return await self._call('save_result_publication', owner, sid, eid, **changes)
            try:
                url = await self.gateway.publish_result(channel.parent, event.user, submission, publication, save)
            except Exception as exc:
                code = exc.code if isinstance(exc, DomainError) else 'result_publish_failed'
                try:
                    await save(error_code=code)
                except Exception:
                    pass  # display failure cannot roll back the already saved evaluation
                detail = exc.message if isinstance(exc, DomainError) else '포럼 연결·봇 권한을 확인한 뒤 /submit 또는 /resume로 게시를 다시 시도하세요.'
                await self._emit(channel, ['제출·평가 기록은 저장했지만 DA-Result 게시를 완료하지 못했습니다. ' + detail])
                for index in range(len(submission['cards'])):
                    try:
                        await self.gateway.send_result_card(channel, submission, index, event.user)
                    except Exception:
                        card = submission['cards'][index]
                        await self._emit(channel, [card['title'] + '\n' + card['description']])
                return
            # Trusted guild/post IDs produce this URL; preserve it as an actual link.
            await self.gateway.send(channel, '정리된 제출 결과: [DA-Result 게시글 열기](' + url + ')\n게시글의 댓글로 결과에 대한 의견을 이어갈 수 있습니다.')

    async def _thread(self, event, session):
        sid = session['session_id']
        lock = self._thread_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            # Replay/concurrent starts may carry an old pre-binding snapshot.
            latest = await self._call('get_session', str(event.user.id), sid)
            return await self._bound_thread(event, latest)

    async def _bound_thread(self, event, session):
        if str(session.get("owner_user_id")) != str(event.user.id) or str(session.get("guild_id")) != str(event.guild.id):
            raise DomainError("discord_session_forbidden", "자신의 서버·과제만 재개할 수 있습니다.", 403)
        thread_id = session.get("thread_id")
        if thread_id:
            channel = await self.gateway.fetch_channel(int(thread_id))
            if channel is not None:
                await self.gateway.validate_thread(channel, event.user)
                return channel
        # Missing/deleted thread: recover only into a validated private parent.
        await self.gateway.validate_parent(event.channel, event.user)
        channel = await self.gateway.create_private_thread(event.channel, event.user, session["session_id"])
        await self._call("bind_thread", str(event.user.id), session["session_id"], str(channel.id))
        return channel

    async def message(self, message):
        if message.author.bot or not message.guild or str(message.guild.id) not in self.guild_ids:
            return
        if not self.gateway.is_private_thread(message.channel):
            return
        # Normalize message author to command event shape without changing Discord objects.
        from types import SimpleNamespace
        event = SimpleNamespace(user=message.author, guild=message.guild, channel=message.channel, id=message.id)
        try:
            session = await self._session(event)
        except Exception:
            return  # unrelated/other-owner messages must never trigger a model call
        text = self.gateway.message_text(message) if hasattr(self.gateway, 'message_text') else message.content
        if not text.strip():
            if not getattr(self.gateway, 'message_content_enabled', True) and not getattr(message, 'attachments', []):
                await self._emit(message.channel, ['일반 메시지 내용은 현재 읽을 수 없습니다. 멤버 검색 결과의 @DA-Agent 봇 계정을 선택해 멘션하고 질문을 보내거나 /query를 사용하세요. 확인 질문의 답변도 같은 방법으로 보내주세요.'])
            return
        try:
            response = await self._call("handle", str(message.author.id), session["session_id"],
                                        str(message.id), "message", text=text)
            await self._emit(message.channel, response.get("messages", []), response.get("tables", []))
        except Exception:
            await self._emit(message.channel, ["처리하지 못했습니다. 기록을 보존했습니다. /resume 으로 재개하세요."])
