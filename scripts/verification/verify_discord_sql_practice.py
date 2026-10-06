"""Real SQL sample in explicit disposable DBs. Never connects/sends to Discord."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_sql_practice import reference_sql
from da_agent.discord_store import DiscordStore
from da_agent.discord_pdf import render_submission_pdf
from da_agent.sql_runner import SqlRunner


def main():
    dsns = [os.environ[key] for key in ('DISCORD_TEST_ADMIN_DSN', 'DISCORD_TEST_LEARNER_DSN', 'DISCORD_TEST_RECORDS_DSN')]
    if any(not conninfo_to_dict(dsn)['dbname'].startswith('discord_test') for dsn in dsns):
        raise ValueError('Explicit disposable discord_test DBs required')
    settings = SimpleNamespace(admin_dsn=dsns[0], learner_dsn=dsns[1], query_timeout_ms=5000,
        max_rows=1000, max_bytes=1048576, preview_rows=1, execution_ttl=600, daily_call_limit=30)
    store = DiscordStore(dsns[2]); store.initialize()
    service = DiscordTrainingService(store, SimpleNamespace(runner=SqlRunner(settings)), None, settings)
    owner = uuid4().hex
    document = service.start(owner, 'verification', 'verification', uuid4().hex, practice='sql', difficulty='advanced')
    if not document.get('session_id'):
        raise RuntimeError(document['messages'][0])
    sid = document['session_id']
    trace = []
    output = Path('tests/artifacts/spec035-2026-10-06')
    output.mkdir(parents=True, exist_ok=True)
    try:
        for action, text in [('sqlrun', '```sql\nSELECT absent_column FROM users\n```'),
                ('sqlrun', '```sql\nWITH answer AS (' + reference_sql(document['task']) + ') SELECT * FROM answer\n```'), ('submit', '')]:
            result = service.handle(owner, sid, uuid4().hex, action, text)
            trace.append(dict(action=action, messages=result['messages'], state=result['session']['state']))
        if not result.get('submission', {}).get('completed'):
            raise RuntimeError('SQL sample not completed')
        public = json.dumps(result['submission'], ensure_ascii=False, indent=2)
        assert all(check['schema_name'] not in public for check in document['sql_checks'])
        assert 'sql_checks' not in public
        output.joinpath('public-submission.json').write_text(public, encoding='utf-8')
        output.joinpath('sql-practice.pdf').write_bytes(render_submission_pdf(result['submission']))
        restored = DiscordTrainingService(DiscordStore(dsns[2]), service.engine, None, settings).resume(owner, 'verification', sid)
        assert len(restored['session']['sql_attempts']) == 2
        output.joinpath('verification.json').write_text(json.dumps(dict(trace=trace, restored=True,
            discord_live='not_deployed_or_sent', public_private_schema_leak=False), ensure_ascii=False, indent=2), encoding='utf-8')
        print('Real PostgreSQL sample, error/revision/evaluation/restart, JSON and PDF verified.')
    finally:
        with psycopg.connect(dsns[0]) as conn:
            for check in document['sql_checks']:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(check['schema_name'])))


if __name__ == '__main__':
    main()
