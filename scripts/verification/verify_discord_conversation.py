"""Live provider + read-only SQL check; never change the learner's saved session."""
import json
import os
import uuid
from pathlib import Path

import psycopg
from psycopg import sql

from da_agent.discord_bot import DiscordSettings
from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.discord_query import DiscordQueryEngine
from da_agent.discord_store import DiscordStore
from da_agent.sql_runner import SqlRunner


def main():
    os.environ['DISCORD_BOT_TOKEN'] = Path('/run/secrets/discord_token').read_text(encoding='utf-8-sig').strip()
    settings = DiscordSettings.from_env()
    store = DiscordStore(settings.records_dsn)
    sid = '1ac939a2-ced5-4519-b2bc-e57a031ebe93'
    with store.connect() as conn:
        owner = conn.execute('SELECT owner_id FROM discord_records.sessions WHERE session_id=%s', (sid,)).fetchone()[0]
    before = store.get(owner, sid)
    provider = DiscordGemmaProvider(key_file=settings.gemini_key_file, model=settings.llm_model)
    engine = DiscordQueryEngine(provider, SqlRunner(settings), settings)
    task = before['task']
    old = {'request': '유저 당 튜토리얼 완료까지 도달할 때 까지 단계별 도전 횟수를 알려줘',
           'answers': [], 'proposed_conditions': {'metric': 'attempts_count', 'unit': 'attempt'}}
    cases = []
    unsupported = engine.resolve(old['request'], task)
    cases.append({'case': 'unsupported', 'state': unsupported['state'], 'reason': unsupported.get('reason')})
    assert unsupported.get('reason') == 'unsupported_query', cases[-1]
    newest = '2026-09-01부터 2026-09-15까지 신규 유저의 튜토리얼 3단계 완료율을 알고싶어'
    plan = engine.resolve(newest, task, pending_query=old)
    cases.append({'case': 'changed_request', 'state': plan['state'], 'conditions': plan.get('conditions', plan.get('proposed_conditions')),
                  'question': plan.get('question'), 'reason': plan.get('reason')})
    assert plan['state'] in {'ready', 'clarification'}
    c = plan.get('conditions', plan.get('proposed_conditions', {}))
    assert c.get('metric') == 'tutorial_rate', cases[-1]
    pending = {'request': newest, 'answers': [], 'proposed_conditions': c}
    complete = '분모는 해당 기간 신규 가입자 전체의 고유 사용자 수, 분자는 3단계 완료한 고유 사용자 수입니다. UTC, 가입 기간은 2026-09-01 이상 2026-09-15 미만, 완료 관측은 2026-09-01 이상 2026-10-01 미만, 전체 집계로 조회해주세요.'
    ready = engine.resolve(complete, task, pending_query=pending)
    cases.append({'case': 'clarification_answer', 'state': ready['state'], 'conditions': ready.get('conditions'), 'reason': ready.get('reason')})
    assert ready['state'] == 'ready', cases[-1]
    outcome = engine.execute('verification-' + uuid.uuid4().hex, before['schema_name'], ready, task)
    assert outcome['state'] == 'success'
    rows = outcome['full_result']['rows']
    with psycopg.connect(settings.learner_dsn) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        reference = conn.execute(sql.SQL('''SELECT count(*), count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM {}.tutorial_attempts a WHERE a.user_id=u.user_id AND a.step=3 AND a.completed
            AND a.attempt_at >= '2026-09-01T00:00:00Z' AND a.attempt_at < '2026-10-01T00:00:00Z'))
            FROM {}.users u WHERE signup_date >= '2026-09-01' AND signup_date < '2026-09-15' ''').format(
                sql.Identifier(before['schema_name']), sql.Identifier(before['schema_name']))).fetchone()
    assert int(rows[0][0]) == reference[0] and int(rows[0][1]) == reference[1]
    assert store.get(owner, sid) == before
    print(json.dumps({'cases': cases, 'sql_rows': rows, 'independent_counts': reference,
                      'training_preserved': True}, ensure_ascii=False, default=str))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'type': type(exc).__name__}))
        raise SystemExit(1) from None
