"""Check a saved task and optionally publish its readable introduction to its owner thread."""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore
from da_agent.discord_transport import safe_chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    token = Path('/run/secrets/discord_token').read_text(encoding='utf-8-sig').strip()

    def api(route, method='GET', body=None):
        request = Request('https://discord.com/api/v10' + route, method=method,
                          headers={'Authorization': 'Bot ' + token, 'Content-Type': 'application/json',
                                   'User-Agent': 'DA-Agent readability verification'},
                          data=json.dumps(body).encode() if body is not None else None)
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    store = DiscordStore(os.environ['DISCORD_RECORDS_DSN'])
    with store.connect() as conn:
        row = conn.execute('SELECT owner_id FROM discord_records.sessions WHERE session_id=%s', (args.session_id,)).fetchone()
    assert row, 'Saved training missing'
    document = store.get(row[0], args.session_id)
    assert document['guild_id'] in os.environ['DISCORD_GUILD_IDS'].split(',')
    thread_id = document['thread_id']
    channel = api('/channels/' + thread_id)
    assert channel['type'] == 12 and not channel['thread_metadata']['invitable']
    assert channel['guild_id'] == document['guild_id']
    assert not channel['thread_metadata']['archived']
    bot_id = api('/users/@me')['id']
    members = api('/channels/' + thread_id + '/thread-members')
    assert {m['user_id'] for m in members} == {bot_id, row[0]}, 'Unexpected thread participants'
    service = DiscordTrainingService(store, None, None, object())
    response = service.resume(row[0], document['guild_id'], args.session_id)
    chunks = [chunk for text in response['messages'] for chunk in safe_chunks(text)]
    assert len(chunks) == 6 and all(len(chunk) <= 1900 for chunk in chunks)
    ids = []
    if args.publish:
        for chunk in chunks:
            message = api('/channels/' + thread_id + '/messages', 'POST',
                          {'content': chunk, 'allowed_mentions': {'parse': []}})
            restored = api('/channels/' + thread_id + '/messages/' + message['id'])
            assert restored['content'] == chunk and not restored['mentions'] and not restored.get('mention_everyone')
            ids.append(message['id'])
    assert store.get(row[0], args.session_id) == document, 'Training record changed'
    print(json.dumps({'session_id': args.session_id, 'thread_id': thread_id, 'sections': len(chunks),
                      'published_message_ids': ids, 'rest_readback_verified': bool(ids),
                      'training_preserved': True, 'screen_review': 'pending'}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # HTTP and DSN exception text can contain credentials.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None
