"""Explicitly archive ONE completed session on live Discord; never sweep sessions.

Runs inside the bot container. Uses real SDK publishing/archiving, with a captured
interaction reply because this diagnostic has no Discord interaction token.
"""
import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

from da_agent.discord_bot import DiscordSettings, create_client
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore
from da_agent.discord_transport import DiscordTransport
from da_agent.errors import DomainError


async def verify(session_id):
    os.environ['DISCORD_BOT_TOKEN'] = Path('/run/secrets/discord_token').read_text(encoding='utf-8-sig').strip()
    settings = DiscordSettings.from_env()
    store = DiscordStore(settings.records_dsn)
    with store.connect() as conn:
        owner = conn.execute('SELECT owner_id FROM discord_records.sessions WHERE session_id=%s', (session_id,)).fetchone()[0]
    original = deepcopy(store.get(owner, session_id))
    assert original['state'] == 'completed' and not original['evaluations'][-1]['result'].get('held')
    eid = original['evaluations'][-1]['id']
    journal = original['result_publications'][eid]
    assert journal['status'] == 'published'
    service = DiscordTrainingService(store, None, None, settings)
    client = create_client(service, settings)
    async def diagnostic_setup():
        pass  # REST diagnostic must not resync the production slash commands.
    client.setup_hook = diagnostic_setup
    transport = next(cell.cell_contents for cell in client.on_message.__closure__
                     if isinstance(cell.cell_contents, DiscordTransport))
    replies = []

    async def capture_reply(event, text):
        replies.append(text)

    transport.gateway.reply = capture_reply
    transport.gateway.defer = lambda event: asyncio.sleep(0)
    try:
        await client.login(settings.token)
        print('REST login complete', flush=True)
        guild = await client.fetch_guild(int(original['guild_id']))
        client._connection._add_guild(guild)
        guild._add_member(await guild.fetch_member(client.user.id))
        user = await guild.fetch_member(int(owner))
        guild._add_member(user)
        parent = await client.fetch_channel(int(original['channel_id']))
        guild._add_channel(parent)
        channel = await transport.gateway.fetch_channel(int(original['thread_id']))
        assert channel is not None, 'Choose an existing completed task thread'
        event = SimpleNamespace(user=user, guild=guild, channel=parent, id=999999999999999999)
        await transport.gateway.validate_thread(channel, user, reopen=False)
        print('Completed task access verified', flush=True)
        await transport._publish_result(event, channel, service.result_submission(owner, session_id))
        archived = await transport.gateway.fetch_channel(channel.id)
        assert archived is not None and archived.archived and archived.locked, 'Thread must be preserved and closed'
        await transport.command(event, 'resume', session_id=session_id)
        assert '보관·잠금' in replies[-1]
        archived = await transport.gateway.fetch_channel(channel.id)
        assert archived.archived and archived.locked, 'Resume must not reopen the task'
        after = store.get(owner, session_id)
        for key in ('reports', 'evaluations', 'messages', 'telemetry', 'thread_id'):
            assert after[key] == original[key], key + ' changed'
        assert after['result_publications'][eid]['post_id'] == journal['post_id']
        post = await client.fetch_channel(int(journal['post_id']))
        starter = await post.fetch_message(post.id)
        assert starter.attachments, 'Existing PDF must be preserved'
        assert not starter.embeds[0].fields, 'Task ID replaces the old source field'
        notices = [m async for m in parent.history(limit=20)
                   if m.author.id == client.user.id and session_id in m.content and str(post.id) in m.content]
        assert notices, 'Persistent result destination missing'
        return dict(session_id=session_id, archived_thread_id=str(channel.id), archived=True, locked=True,
                    post_id=str(post.id), post_url=f'https://discord.com/channels/{guild.id}/{post.id}',
                    records_preserved=True, pdf_preserved=True, resume_recreates_thread=False,
                    parent_notice_id=str(notices[0].id), interaction_reply='captured; UI verification separate')
    finally:
        await client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--session-id', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(asyncio.wait_for(verify(args.session_id), timeout=90)), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__,
                          'error_code': exc.code if isinstance(exc, DomainError) else None}))
        raise SystemExit(1) from None
