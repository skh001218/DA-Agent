"""Read-only live-provider checks; never write a learner session or post to Discord.

Run with the changed source in an isolated diagnostic container and the existing
read-only dataset connection. Only public query results are written to --output.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace as NS
import uuid

import psycopg
from psycopg import sql

from da_agent.discord_generated_query import GeneratedQueryEngine
from da_agent.discord_provider import configured_discord_provider
from da_agent.discord_service import format_result
from da_agent.discord_tables import result_table, render_table_png
from da_agent.sql_runner import SqlRunner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--thread', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(os.environ['DISCORD_RECORDS_DSN']) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        session_id, before = conn.execute('SELECT session_id,document FROM discord_records.sessions WHERE thread_id=%s', (args.thread,)).fetchone()
    settings = NS(learner_dsn=os.environ['DISCORD_LEARNER_DSN'], query_timeout_ms=5000,
        max_rows=1000, max_bytes=1048576, preview_rows=200, execution_ttl=600,
        llm_provider=os.environ.get('DISCORD_LLM_PROVIDER', 'gemma'),
        gemini_key_file=Path(os.environ.get('DISCORD_GEMINI_KEY_FILE', '/run/secrets/gemma_key')),
        llm_model=os.environ.get('DISCORD_MODEL', 'gemma-4-26b-a4b-it'),
        codex_bin=os.environ.get('DISCORD_CODEX_BIN', 'codex'),
        codex_model=os.environ.get('DISCORD_CODEX_MODEL') or None, codex_timeout_seconds=180)
    cases = [
        ('missing', '상점의 판매 아이템 목록이 이전과 어떻게 달라졌어?', None),
        ('all_rows', 'shop_profiles의 전체 데이터를 조회해줘', 'SELECT id,shop_version,prior_mastery FROM shop_profiles ORDER BY id'),
        ('filter', 'shop_profiles에서 사전 숙련도가 beginner인 계정의 id, shop_version, prior_mastery를 id 오름차순으로 조회해줘', "SELECT id,shop_version,prior_mastery FROM shop_profiles WHERE prior_mastery='beginner' ORDER BY id"),
        ('compare', '상점 구성별 초기 평균 소모, 후기 평균 소모, 후기 소모에서 초기 소모를 뺀 평균 변화량을 조회해줘. shop_version 오름차순, 컬럼 순서는 상점 구성, 초기 평균, 후기 평균, 변화량이야.', 'SELECT p.shop_version,AVG(c.early_spent),AVG(c.late_spent),AVG(c.late_spent-c.early_spent) FROM account_currency c JOIN shop_profiles p ON c.profile_id=p.id GROUP BY p.shop_version ORDER BY p.shop_version'),
    ]
    evidence = dict(thread_id=args.thread, cases=[])
    # Isolate CLI authentication/cache writes from the running bot's home.
    with tempfile.TemporaryDirectory(prefix='query-verification-') as temp:
        source_home = os.environ.get('DISCORD_CODEX_HOME')
        settings.codex_home = str(Path(temp) / 'codex')
        if source_home and settings.llm_provider == 'codex_cli':
            shutil.copytree(source_home, settings.codex_home)
        provider = configured_discord_provider(settings)
        engine = GeneratedQueryEngine(provider, SqlRunner(settings), settings)
        for name, request, reference in cases:
            plan = engine.resolve(request, before['task'])
            entry = dict(case=name, request=request, plan=plan)
            if reference is None:
                assert plan.get('reason') == 'missing_data', plan
                assert '아이템' in plan['message']
            else:
                assert plan['state'] == 'ready', plan
                outcome = engine.execute('verification-' + uuid.uuid4().hex, before['schema_name'], plan, before['task'])
                assert outcome['state'] == 'success', outcome
                full = outcome['full_result']
                with psycopg.connect(settings.learner_dsn) as conn:
                    conn.execute('SET TRANSACTION READ ONLY')
                    conn.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(before['schema_name'])))
                    expected = conn.execute(reference).fetchall()
                from da_agent.sql_runner import encode
                assert full['rows'] == [encode(row) for row in expected], name
                assert full['result_complete']
                entry.update(row_count=len(full['rows']), result=full)
                if name == 'all_rows':
                    assert len(full['rows']) == 200
                    execution = dict(execution_id=full['execution_id'], conditions=plan['conditions'],
                        data_version=before['data_version'], result=full)
                    table = result_table(execution)
                    assert len(json.loads(table['download']['content'])['rows']) == 200
                    (directory / 'full-data.json').write_text(table['download']['content'], encoding='utf-8')
                    (directory / 'response.txt').write_text(format_result(execution, settings), encoding='utf-8')
                    for index, page in enumerate(render_table_png(table), 1):
                        (directory / f'preview-{index}.png').write_bytes(page)
            entry['passed'] = True
            evidence['cases'].append(entry)
            (directory / 'verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(dict(case=name, passed=True, state=plan['state'], row_count=entry.get('row_count'), message=plan.get('message')), ensure_ascii=False), flush=True)
    with psycopg.connect(os.environ['DISCORD_RECORDS_DSN']) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        after = conn.execute('SELECT document FROM discord_records.sessions WHERE session_id=%s', (session_id,)).fetchone()[0]
    assert before == after, 'Learner session changed during verification'
    evidence['learner_session_preserved'] = True
    (directory / 'verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(learner_session_preserved=True)), flush=True)


if __name__ == '__main__':
    main()
