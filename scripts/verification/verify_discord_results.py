"""Read back a real, owner-scoped forum publication without writing Discord/DB."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

from da_agent.discord_results import build_submission, card_marker
from da_agent.discord_forum import ResultForumPublisher
from da_agent.discord_pdf import pdf_filename
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
    starter = next(m for m in messages if m['id'] == post['id'])
    assert starter['author']['id'] == bot_id, 'Unexpected starter author'
    embeds = starter.get('embeds', [])
    assert embeds and embeds[0].get('footer', {}).get('text') == card_marker(submission, 0)
    user = SimpleNamespace(display_name=embeds[0]['author']['name'])
    expected = ResultForumPublisher(None).post_embeds(submission, user)
    assert len(embeds) == len(expected), 'Missing or duplicate starter section'
    for embed, wanted in zip(embeds, expected):
        assert embed['description'] == wanted.description and embed['title'] == wanted.title
    assert len(embeds) <= 10 and sum(len(e) for e in expected) <= 6000
    assert any(a['filename'] == pdf_filename(submission) for a in starter.get('attachments', [])), 'Full PDF missing'
    result_replies = [m for m in messages if m['id'] != starter['id'] and m['author']['id'] == bot_id
                      and any((e.get('footer', {}).get('text') or '').startswith(('결과 ', 'DA-Result · '))
                              for e in m.get('embeds', []))]
    assert all(not m.get('mention_everyone') and not m.get('mentions') for m in messages if m['author']['id'] == bot_id)
    assert store.get(owner, args.session_id) == doc, 'Read-only verification changed a record'
    result = dict(session_id=args.session_id, evaluation_id=submission['evaluation_id'], forum_id=forum['id'],
                  post_id=post['id'], post_url=f"https://discord.com/channels/{doc['guild_id']}/{post['id']}",
                  evaluation_count=len(doc['evaluations']), evaluation_total=doc['evaluations'][-1]['result']['total'],
                  telemetry_count=len(doc['telemetry']), card_count=len(submission['cards']),
                  starter_embed_count=len(embeds), legacy_result_reply_count=len(result_replies),
                  rest_content_matches=True, full_pdf_attached=True, mentions_disabled=True, records_preserved=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None
