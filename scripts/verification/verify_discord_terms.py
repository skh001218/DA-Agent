"""Read-only confirmation of the two UI term questions in the live records DB."""
import json
import os

from da_agent.discord_store import DiscordStore


def main():
    store = DiscordStore(os.environ['DISCORD_RECORDS_DSN'])
    with store.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        sid = conn.execute('SELECT session_id FROM discord_records.sessions WHERE thread_id=%s',
                           ('1556930261298585680',)).fetchone()[0]
        events = conn.execute('SELECT request,response FROM discord_records.events WHERE session_id=%s AND response IS NOT NULL ORDER BY created_at', (sid,)).fetchall()
    term_indices = [i for i, (request, _) in enumerate(events) if request.get('action') == 'question']
    assert len(term_indices) >= 2
    first, second = term_indices[-2:]
    before = events[first - 1][1]['session']
    stable = ('state', 'pending_question', 'pending_query', 'queries', 'executions',
              'selected_evidence', 'reports', 'evaluations', 'conditions')
    cases = []
    for index in (first, second):
        request, response = events[index]
        session = response['session']
        assert all(before.get(key) == session.get(key) for key in stable)
        assert all(1 <= len(block.splitlines()) <= 3 for block in response['messages'])
        assert all(len(line) <= 120 for block in response['messages'] for line in block.splitlines())
        delta = len([t for t in session['telemetry'] if t['kind'] == 'model']) - len([t for t in before['telemetry'] if t['kind'] == 'model'])
        cases.append({'question': request['text'], 'answers': response['messages'],
                      'model_calls': delta, 'analysis_preserved': True})
        before = session
    assert cases[0]['model_calls'] == 0
    assert cases[1]['model_calls'] == 1
    print(json.dumps({'session_id': sid, 'cases': cases}, ensure_ascii=False))


if __name__ == '__main__':
    main()
