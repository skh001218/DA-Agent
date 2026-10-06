"""Render saved public evidence locally and inspect real Discord attachments."""
import argparse
import json
import os
from pathlib import Path

from urllib.request import Request, urlopen

from da_agent.discord_store import DiscordStore
from da_agent.discord_tables import dictionary_tables, result_table, render_table_png


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    store = DiscordStore(os.environ['DISCORD_RECORDS_DSN'])
    with store.connect() as conn:
        owner = conn.execute('SELECT owner_id FROM discord_records.sessions WHERE session_id=%s', (args.session_id,)).fetchone()[0]
    document = store.get(owner, args.session_id)
    assert document['guild_id'] in os.environ['DISCORD_GUILD_IDS'].split(',')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    tables = dictionary_tables(document['task'])
    if document['executions']:
        tables.append(result_table(document['executions'][-1]))
    paths = []
    for index, table in enumerate(tables, 1):
        for page, image in enumerate(render_table_png(table), 1):
            path = output / f'table-{index}-{page}.png'
            path.write_bytes(image)
            paths.append(path.name)
    token = Path('/run/secrets/discord_token').read_text(encoding='utf-8-sig').strip()
    def api(route):
        request = Request('https://discord.com/api/v10' + route, headers={
            'Authorization': 'Bot ' + token, 'User-Agent': 'DA-Agent verification'})
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    thread = api('/channels/' + document['thread_id'])
    assert thread['type'] == 12 and not thread['thread_metadata']['invitable']
    messages = api('/channels/' + document['thread_id'] + '/messages?limit=12')
    attachments = [{'message_id': m['id'], 'filename': a['filename'], 'width': a.get('width'),
                    'height': a.get('height')} for m in messages for a in m.get('attachments', [])]
    assert store.get(owner, args.session_id) == document
    report = dict(rendered=paths, attachments=attachments, training_preserved=True,
                  saved_execution_count=len(document['executions']), saved_model_calls=len(document['telemetry']))
    (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None
