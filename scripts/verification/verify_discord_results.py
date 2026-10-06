"""Read back a real, owner-scoped forum publication without writing Discord/DB."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from da_agent.discord_results import build_submission, card_marker
from da_agent.discord_store import DiscordStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    store = DiscordStore(os.environ['DISCORD_RECORDS_DSN'])
    with store.connect() as conn:
        owner = conn.execute('SELECT owner_id FROM discord_records.sessions WHERE session_id=%s', (args.session_id,)).fetchone()[0]
    doc = store.get(owner, args.session_id)
    assert doc['guild_id'] in os.environ['DISCORD_GUILD_IDS'].split(',')
    submission = build_submission(doc)
    publication = doc.get('result_publications', {}).get(submission['evaluation_id'], {})
    assert publication.get('status') == 'published', 'Publication not complete'
    token = Path('/run/secrets/discord_token').read_text(encoding='utf-8-sig').strip()
    def api(route):
        request = Request('https://discord.com/api/v10' + route, headers={
            'Authorization': 'Bot ' + token, 'User-Agent': 'DA-Agent verification'})
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    forum = api('/channels/' + publication['forum_id'])
    assert forum['type'] == 15 and forum['name'].casefold() == 'da-result'
    post = api('/channels/' + publication['post_id'])
    assert post['parent_id'] == publication['forum_id'] and post['guild_id'] == doc['guild_id']
    bot_id = api('/users/@me')['id']
    messages, before = [], None
    while True:
        page = api('/channels/' + post['id'] + '/messages?limit=100' + ('&before=' + before if before else ''))
        messages.extend(page)
        if len(page) < 100: break
        before = page[-1]['id']
    embeds = [e for m in messages if m['author']['id'] == bot_id for e in m.get('embeds', [])]
    markers = Counter(e.get('footer', {}).get('text') for e in embeds)
    for index, card in enumerate(submission['cards']):
        marker = card_marker(submission, index)
        assert markers[marker] == 1, 'Missing or duplicate result card'
        embed = next(e for e in embeds if e.get('footer', {}).get('text') == marker)
        assert embed['description'] == card['description'] and embed['title'] == card['title'][:256]
    assert all(not m.get('mention_everyone') and not m.get('mentions') for m in messages if m['author']['id'] == bot_id)
    assert store.get(owner, args.session_id) == doc, 'Read-only verification changed a record'
    result = dict(session_id=args.session_id, evaluation_id=submission['evaluation_id'], forum_id=forum['id'],
                  post_id=post['id'], post_url=f"https://discord.com/channels/{doc['guild_id']}/{post['id']}",
                  evaluation_count=len(doc['evaluations']), evaluation_total=doc['evaluations'][-1]['result']['total'],
                  telemetry_count=len(doc['telemetry']), card_count=len(submission['cards']),
                  rest_content_matches=True, duplicate_cards=0, mentions_disabled=True, records_preserved=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None
