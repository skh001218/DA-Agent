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
    ACTIONS = {"query", "sqlrun", "answer", "question", "help", "report", "followup", "submit", "sql", "evidence", "end", "message"}

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

    async def _emit(self, channel, messages, session=None, tables=()):
        from .discord_tables import table_fallback
        for index, message in enumerate(messages):
            ids = []
            from .discord_sql_practice import TEMPLATE
            # Only this constant authored template uses native Markdown fences.
            for chunk in ([TEMPLATE] if (session or {}).get('practice') == 'sql' and message == TEMPLATE else safe_chunks(message)):
                sent = await self.gateway.send(channel, chunk)
                if getattr(sent, 'id', None) is not None:
                    ids.append(str(sent.id))
            if ids and (session or {}).get('practice') == 'sql' and (message == TEMPLATE or message.startswith('SQL 실행')):
                attempts = session.get('sql_attempts', [])
                await self._call('bind_sql_prompt', session['owner_user_id'], session['session_id'], ids,
                                 attempts[-1]['execution_id'] if attempts else None)
            question = (session or {}).get('pending_question')
            if ids and question and message == question['text']:
                await self._call('bind_question', session['owner_user_id'], session['session_id'], question['id'], ids)
            for table in tables:
                if table.get('after_message', 0) != index:
                    continue
                sql_result = (session or {}).get('practice') == 'sql' and session.get('sql_attempts') and table.get('title') == '조회 결과'
                try:
                    table_ids = await self.gateway.send_table(channel, table)
                    if table_ids and sql_result:
                        await self._call('bind_sql_prompt', session['owner_user_id'], session['session_id'], table_ids,
                                         session['sql_attempts'][-1]['execution_id'])
                except Exception:
                    for chunk in safe_chunks('표 첨부를 표시하지 못해 행별 목록으로 제공합니다.\n' + table_fallback(table)):
                        sent = await self.gateway.send(channel, chunk)
                        if getattr(sent, 'id', None) and sql_result:
                            await self._call('bind_sql_prompt', session['owner_user_id'], session['session_id'], [str(sent.id)],
                                             session['sql_attempts'][-1]['execution_id'])

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
        if session.get('state') == 'completed':
            await self.gateway.validate_thread(event.channel, event.user, reopen=False)
        else:
            await self.gateway.validate_thread(event.channel, event.user)
        return session

    async def command(self, event, action, *, session_id=None, text="", payload=None,
                      topic="tutorial", difficulty="intermediate", help_level=None, practice=None, source_session_id=None):
        # First operation is the acknowledgement; model/DB work never precedes it.
        try:
            await self.gateway.defer(event)
            self._guild(event)
            owner, guild, event_id = str(event.user.id), str(event.guild.id), str(event.id)
            if action == "tip":
                from .discord_presentation import command_tip
                for chunk in safe_chunks(command_tip(text)):
                    await self.gateway.reply(event, chunk)
                return
            if action == 'history':
                messages = await self._call('history', owner, guild, (payload or {}).get('page', 1))
                for message in messages:
                    await self.gateway.reply(event, message)
                return
            if action == "training":
                await self.gateway.validate_parent(event.channel, event.user)
                session = await self._call("start", owner, guild, str(event.channel.id), event_id,
                                           topic=topic, difficulty=difficulty, help_level=help_level, practice=practice, source_session_id=source_session_id)
                if not session.get("session_id"):
                    for message in session.get("messages", ["과제 준비에 실패했습니다. 다시 시작하세요."]):
                        for chunk in safe_chunks(message):
                            await self.gateway.reply(event, chunk)
                    return
                channel = await self._thread(event, session)
                response = await self._call("resume", owner, guild, session["session_id"])
                await self._emit(channel, response.get("messages", []), response.get('session'), response.get('tables', []))
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
                submission = response.get('submission')
                if submission and submission.get('completed'):
                    publication = await self._call('result_publication', owner, session['session_id'], submission['evaluation_id'])
                    if publication.get('status') == 'published':
                        channel = await self.gateway.fetch_channel(int(session['thread_id'])) if session.get('thread_id') else None
                        if channel is not None:
                            await self.gateway.validate_thread(channel, event.user, reopen=False)
                            parent = channel.parent
                        else:
                            parent = await self.gateway.fetch_channel(int(session['channel_id']))
                            await self.gateway.validate_parent(parent, event.user)
                        await self._publish_result(event, channel, submission, parent=parent)
                        return
                channel = await self._thread(event, session)
                if session.get("state") in {"stopped", "interrupted"}:
                    await self._call("handle", owner, session["session_id"], event_id, "continue")
                    response = await self._call("resume", owner, guild, session["session_id"])
                if not (response.get('submission') or {}).get('completed'):
                    await self._emit(channel, response.get("messages", []), response.get('session'), response.get('tables', []))
                if response.get('submission'):
                    await self._publish_result(event, channel, response['submission'])
                    return
                await self.gateway.reply(event, f"재개 공간: <#{channel.id}>")
                return
            if action not in self.ACTIONS:
                raise DomainError("discord_unknown_action", "지원하지 않는 명령입니다.")
            session = await self._session(event, session_id)
            revising_completed = session.get('state') == 'completed' and action in {'report', 'sqlrun'}
            if session.get('state') == 'completed' and action not in {'submit', 'report', 'sqlrun', 'sql', 'help'}:
                await self.gateway.reply(event, '완료된 과제입니다. /resume으로 결과·대화를 열람하거나 /history로 연습 기록을 확인하세요. 보고 수정은 /report로 시작하세요.')
                return
            response = await self._call("handle", owner, session["session_id"], event_id, action, text=text, payload=payload)
            if session.get('state') == 'completed' and action in {'sql', 'help'}:
                for message in response.get('messages', []):
                    for chunk in safe_chunks(message):
                        await self.gateway.reply(event, chunk)
                return
            if revising_completed:
                if response.get('session', {}).get('state') == 'completed':
                    for message in response.get('messages', []):
                        for chunk in safe_chunks(message):
                            await self.gateway.reply(event, chunk)
                    return
                # Reopen only after a valid revision is stored. Reading/replaying
                # completed work keeps its archived thread and result intact.
                await self.gateway.validate_thread(event.channel, event.user)
            if not (response.get('submission') or {}).get('completed'):
                await self._emit(event.channel, response.get("messages", []), response.get('session'), response.get('tables', []))
            if response.get('submission'):
                await self._publish_result(event, event.channel, response['submission'])
                return
            acknowledgement = {'clarification': '조회에 필요한 조건을 확인 중입니다. 스레드의 질문에 답해주세요.',
                               'error': '조회하지 못했습니다. 스레드의 오류 안내를 확인하세요.',
                               'success': '조회 결과를 저장했습니다. 스레드에서 확인하세요.'}
            await self.gateway.reply(event, acknowledgement.get(response.get('request_state'), '응답을 스레드에 남겼습니다. 내용을 확인하세요.'))
        except asyncio.CancelledError:
            if hasattr(self.gateway, 'interrupt'):
                await asyncio.shield(self.gateway.interrupt(event))
            raise
        except Exception as exc:
            # Do not expose exception strings: HTTP/DB errors can include credentials.
            message = exc.message if isinstance(exc, DomainError) else "처리하지 못했습니다. 기록은 보존됩니다. 권한·연결 상태를 확인한 뒤 /resume 으로 재개하세요."
            await self.gateway.reply(event, safe_chunks(message)[0])
        finally:
            if hasattr(self.gateway, 'finish'):
                self.gateway.finish(event)

    async def _publish_result(self, event, channel, submission, *, parent=None):
        owner, sid, eid = str(event.user.id), submission['session_id'], submission['evaluation_id']
        if submission['owner_user_id'] != owner or submission['guild_id'] != str(event.guild.id):
            raise DomainError('result_owner', '자신의 서버·제출 결과만 게시할 수 있습니다.', 403)
        lock = self._result_locks.setdefault(str(event.guild.id), asyncio.Lock())
        async with lock:
            publication = await self._call('result_publication', owner, sid, eid)
            async def save(**changes):
                return await self._call('save_result_publication', owner, sid, eid, **changes)
            try:
                url = await self.gateway.publish_result(parent or channel.parent, event.user, submission, publication, save)
            except Exception as exc:
                code = exc.code if isinstance(exc, DomainError) else 'result_publish_failed'
                try:
                    await save(error_code=code)
                except Exception:
                    pass  # display failure cannot roll back the already saved evaluation
                detail = exc.message if isinstance(exc, DomainError) else '포럼 연결·봇 권한을 확인한 뒤 /submit 또는 /resume로 게시를 다시 시도하세요.'
                if channel is None:
                    await self.gateway.reply(event, '제출·평가 기록은 보존했습니다. DA-Result 게시를 확인하지 못했습니다. ' + detail)
                    return
                await self._emit(channel, ['제출·평가 기록은 저장했지만 DA-Result 게시를 완료하지 못했습니다. ' + detail])
                for index in range(len(submission['cards'])):
                    try:
                        await self.gateway.send_result_card(channel, submission, index, event.user)
                    except Exception:
                        card = submission['cards'][index]
                        await self._emit(channel, [card['title'] + '\n' + card['description']])
                await self.gateway.reply(event, '제출 기록을 보존했습니다. 과제 스레드의 게시 오류 안내를 확인하세요.')
                return
            # Trusted guild/post IDs produce this URL; preserve it as an actual link.
            notice = '정리된 제출 결과: [DA-Result 게시글 열기](' + url + ')\n게시글의 댓글로 결과에 대한 의견을 이어갈 수 있습니다.'
            if not submission.get('completed'):
                await self.gateway.send(channel, notice)
                await self.gateway.reply(event, notice + '\n평가 보류 상태이므로 과제 스레드를 보존합니다.')
                return
            if channel is None:
                await self.gateway.reply(event, notice + '\n완료된 과제입니다. 과제 스레드를 다시 만들지 않습니다.')
                return
            conversation = f'https://discord.com/channels/{event.guild.id}/{channel.id}'
            notice += '\n과거 대화: [과제 스레드 열람](' + conversation + ')'
            # A completed thread is read-only, including on /resume and /submit replay.
            try:
                latest = await self._call('get_session', owner, sid)
                if (str(channel.id) != str(latest.get('thread_id')) or latest.get('state') != 'completed'
                        or latest['evaluations'][-1]['id'] != eid):
                    raise DomainError('result_cleanup_thread', '과제 스레드 연결을 확인해주세요.')
                if not getattr(channel, 'archived', False):
                    await self.gateway.send(channel, notice + '\n완료된 과제의 대화를 보관합니다. 의견은 결과 포럼에서 이어가세요.')
                    await self.gateway.send(parent or channel.parent, notice + '\n과제 ID: ' + sid)
                await self.gateway.archive_task_thread(channel, event.user)
                await self.gateway.reply(event, notice + '\n완료된 과제 스레드는 보관·잠금 상태로 유지합니다.')
            except Exception:
                await self.gateway.reply(event, notice + '\n결과는 게시했지만 과제 스레드 보관 안내를 완료하지 못했습니다. 기록은 보존됩니다. 봇의 스레드 관리·발언 권한과 연결 상태를 확인한 뒤 /resume으로 재시도하세요.')

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
                if session.get('state') == 'completed':
                    await self.gateway.validate_thread(channel, event.user, reopen=False)
                else:
                    await self.gateway.validate_thread(channel, event.user)
                return channel
        # Missing/deleted thread: recover only into a validated private parent.
        await self.gateway.validate_parent(event.channel, event.user)
        session = await self._call('reserve_thread_name', str(event.user.id), session['session_id'])
        channel = await self.gateway.create_private_thread(event.channel, event.user, session["session_id"], name=session['thread_name'])
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
        if session.get('state') == 'completed' and session.get('practice') != 'sql':
            return  # Archived training remains read-only; discussion belongs in the result forum.
        # Preserve SQL literals verbatim; stripping mentions across the entire
        # message could alter a quoted learner SQL value. Outside-block mentions
        # are ignored by the fenced SQL extractor instead.
        text = message.content if session.get('practice') == 'sql' else (self.gateway.message_text(message) if hasattr(self.gateway, 'message_text') else message.content)
        reference = getattr(message, 'reference', None)
        reply_id = getattr(reference, 'message_id', None)
        if session.get('practice') == 'sql':
            if not reply_id or str(reply_id) not in session.get('sql_reply_targets', {}):
                return
            if not text.strip():
                await self._emit(message.channel, ['답장 내용을 읽을 수 없습니다. 봇 멘션을 포함해 sql 코드 블록으로 답장하거나 /sqlrun text:코드블록을 사용하세요.'])
                return
            try:
                response = await self._call('handle', str(message.author.id), session['session_id'], str(message.id),
                    'sqlrun', text=text, payload={'reply_to_message_id': str(reply_id)})
                if session.get('state') == 'completed' and response.get('session', {}).get('state') != 'completed':
                    await self.gateway.validate_thread(message.channel, message.author)
                await self._emit(message.channel, response.get('messages', []), response.get('session'), response.get('tables', []))
            except Exception as exc:
                await self._emit(message.channel, [exc.message if isinstance(exc, DomainError) else 'SQL 요청을 처리하지 못했습니다. /resume으로 기록을 확인하세요.'])
            return
        bot_id = getattr(self.gateway, 'bot_user_id', None)
        mentioned = bot_id is not None and any(str(user.id) == str(bot_id) for user in getattr(message, 'mentions', []))
        if not reply_id and not mentioned:
            return  # ordinary discussion never silently becomes a query or an answer
        if not text.strip():
            if not getattr(self.gateway, 'message_content_enabled', True) and not getattr(message, 'attachments', []):
                await self._emit(message.channel, ['답장 내용을 읽을 수 없습니다. @DA-Agent 봇 계정을 선택해 멘션과 함께 답하거나 /answer를 사용하세요. 새 조회는 /query로 요청하세요.'])
            return
        try:
            response = await self._call("handle", str(message.author.id), session["session_id"],
                                        str(message.id), "answer", text=text,
                                        payload={'reply_to_message_id': str(reply_id) if reply_id else None})
            await self._emit(message.channel, response.get("messages", []), response.get('session'), response.get('tables', []))
        except Exception:
            await self._emit(message.channel, ["처리하지 못했습니다. 기록을 보존했습니다. /resume 으로 재개하세요."])
