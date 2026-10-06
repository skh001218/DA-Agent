import asyncio
import datetime as dt
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx

from da_agent.discord_responses import DiscordResponses, INTERRUPTED


def event(event_id=123):
    return NS(id=event_id, application_id=456, token='private-interaction-token',
              expires_at=dt.datetime.fromtimestamp(1000, dt.timezone.utc),
              response=NS(defer=AsyncMock()), edit_original_response=AsyncMock(),
              followup=NS(send=AsyncMock()), is_expired=lambda: False)


def test_restart_updates_original_and_removes_token(tmp_path):
    first = DiscordResponses(tmp_path, clock=lambda: 100)
    e = event()
    asyncio.run(first.defer(e))
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == 'PATCH'
        assert request.url.path.endswith('/messages/@original')
        assert json.loads(request.content)['content'] == INTERRUPTED
        assert json.loads(request.content)['allowed_mentions'] == {'parse': []}
        return httpx.Response(200, json={'content': INTERRUPTED, 'flags': 64})
    async def restart():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            second = DiscordResponses(tmp_path, clock=lambda: 200)
            assert await second.recover(client) == {'updated': 1, 'expired': 0, 'retry': 0}
            await second.recover(client)
    asyncio.run(restart())
    assert len(calls) == 1
    assert not list(tmp_path.glob('*.json'))


def test_normal_reply_replaces_loading_then_sends_additional_chunks(tmp_path):
    responses = DiscordResponses(tmp_path)
    e = event()
    async def run():
        await responses.defer(e)
        await responses.reply(e, '완료', None)
        await responses.reply(e, '추가 안내', None)
        responses.finish(e.id)
    asyncio.run(run())
    e.edit_original_response.assert_awaited_once_with(content='완료', allowed_mentions=None)
    e.followup.send.assert_awaited_once_with('추가 안내', ephemeral=True, allowed_mentions=None)
    assert not responses.replied
    assert not list(tmp_path.glob('*.json'))


def test_active_requests_are_preserved_and_shutdown_interrupts(tmp_path):
    responses = DiscordResponses(tmp_path, clock=lambda: 100)
    responses.remember(event())
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await responses.recover(client)
            assert not calls
            await responses.recover(client, include_active=True)
    asyncio.run(run())
    assert len(calls) == 1


def test_expired_token_is_removed_without_network_request(tmp_path):
    DiscordResponses(tmp_path).remember(event())
    responses = DiscordResponses(tmp_path, clock=lambda: 1001)
    client = NS(patch=AsyncMock())
    assert asyncio.run(responses.recover(client))['expired'] == 1
    client.patch.assert_not_awaited()
    assert not list(tmp_path.glob('*.json'))


def test_network_failure_and_rate_limit_retry_without_exposing_tokens(tmp_path):
    responses = DiscordResponses(tmp_path, clock=lambda: 100)
    responses.remember(event())
    responses.active.clear()
    client = NS(patch=AsyncMock(side_effect=httpx.ConnectError('connection failed')))
    assert asyncio.run(responses.recover(client))['retry'] == 1
    assert list(tmp_path.glob('*.json'))
    client.patch = AsyncMock(return_value=httpx.Response(429))
    assert asyncio.run(responses.recover(client))['retry'] == 1
    assert list(tmp_path.glob('*.json'))
    client.patch = AsyncMock(return_value=httpx.Response(404))
    assert asyncio.run(responses.recover(client))['expired'] == 1
    assert not list(tmp_path.glob('*.json'))


def test_cancelled_request_updates_original_or_preserves_for_retry(tmp_path):
    responses = DiscordResponses(tmp_path, clock=lambda: 100)
    e = event()
    responses.remember(e)
    e.edit_original_response.side_effect = RuntimeError('unavailable')
    asyncio.run(responses.interrupt(e, None))
    assert str(e.id) not in responses.active
    assert list(tmp_path.glob('*.json'))
    e.edit_original_response.side_effect = None
    asyncio.run(responses.interrupt(e, None))
    assert not list(tmp_path.glob('*.json'))


def test_cancelled_command_preserves_cancellation_and_clears_loading():
    from da_agent.discord_transport import DiscordTransport
    gateway = NS(defer=AsyncMock(), interrupt=AsyncMock(), finish=Mock())
    transport = DiscordTransport(NS(), gateway, [10])
    transport._session = AsyncMock(side_effect=asyncio.CancelledError())
    e = NS(id=123, guild=NS(id=10), user=NS(id=1))
    async def run():
        try:
            await transport.command(e, 'query')
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('Cancellation was swallowed')
    asyncio.run(run())
    gateway.interrupt.assert_awaited_once_with(e)
    gateway.finish.assert_called_once_with(e)


def test_reply_failure_becomes_recoverable_after_command_finishes(tmp_path):
    responses = DiscordResponses(tmp_path, clock=lambda: 100)
    e = event()
    responses.remember(e)
    e.edit_original_response.side_effect = RuntimeError('network unavailable')
    async def run():
        try:
            await responses.reply(e, '완료', None)
        except RuntimeError:
            responses.finish(e.id)
        client = NS(patch=AsyncMock(return_value=httpx.Response(200)))
        assert (await responses.recover(client))['updated'] == 1
    asyncio.run(run())
    assert not list(tmp_path.glob('*.json'))
