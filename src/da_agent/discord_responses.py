"""Short-lived private response recovery; never log interaction webhook tokens."""
import asyncio
import json
import os
import time
from pathlib import Path

import httpx


INTERRUPTED = '요청의 응답이 중단되었습니다. /resume으로 저장된 결과를 확인하세요. 요청을 자동으로 다시 실행하지 않습니다.'


class DiscordResponses:
    def __init__(self, directory, *, clock=time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.clock = clock
        self.active = set()
        self.replied = set()
        # An incomplete atomic write cannot recover a token; do not retain it.
        for temporary in self.directory.glob('*.tmp'):
            temporary.unlink(missing_ok=True)

    def _path(self, event_id):
        value = str(event_id)
        if not value.isdecimal():
            raise ValueError('Invalid interaction ID')
        return self.directory / (value + '.json')

    def remember(self, event):
        path = self._path(event.id)
        data = {'id': str(event.id), 'application_id': str(event.application_id),
                'token': event.token, 'expires_at': event.expires_at.timestamp()}
        temporary = path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
        temporary.replace(path)
        self.active.add(str(event.id))

    def forget(self, event_id):
        self._path(event_id).unlink(missing_ok=True)
        self.active.discard(str(event_id))

    async def defer(self, event):
        await event.response.defer(ephemeral=True, thinking=True)
        self.remember(event)

    async def reply(self, event, text, allowed_mentions):
        if event.is_expired():
            self.forget(event.id)
            self.replied.discard(str(event.id))
            return
        if str(event.id) in self.replied:
            await event.followup.send(text, ephemeral=True, allowed_mentions=allowed_mentions)
        else:
            # Editing @original explicitly replaces Discord's loading response.
            await event.edit_original_response(content=text, allowed_mentions=allowed_mentions)
            self.forget(event.id)
            self.replied.add(str(event.id))

    def finish(self, event_id):
        self.replied.discard(str(event_id))
        self.active.discard(str(event_id))

    async def interrupt(self, event, allowed_mentions):
        if event.is_expired():
            self.forget(event.id)
            return
        try:
            await event.edit_original_response(content=INTERRUPTED, allowed_mentions=allowed_mentions)
        except Exception:
            # Keep the token for recovery. Exception strings may contain it.
            self.active.discard(str(event.id))
        else:
            self.forget(event.id)
        self.finish(event.id)

    async def recover(self, client=None, *, include_active=False):
        if client is None:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as http:
                return await self.recover(http, include_active=include_active)
        counts = {'updated': 0, 'expired': 0, 'retry': 0}
        for path in self.directory.glob('*.json'):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if data['expires_at'] <= self.clock():
                    self.forget(path.stem)
                    counts['expired'] += 1
                    continue
                if not include_active and path.stem in self.active:
                    continue
                # This is an interaction token, not the global bot credential.
                url = f"https://discord.com/api/v10/webhooks/{data['application_id']}/{data['token']}/messages/@original"
                response = await client.patch(url, json={'content': INTERRUPTED, 'allowed_mentions': {'parse': []}})
                if response.is_success or response.status_code in {401, 403, 404}:
                    self.forget(path.stem)
                    counts['updated' if response.is_success else 'expired'] += 1
                else:
                    counts['retry'] += 1
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                counts['retry'] += 1
        return counts

    async def maintenance(self):
        while True:
            await asyncio.sleep(30)
            await self.recover()
