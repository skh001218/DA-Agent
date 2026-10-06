"""Disposable browser fixture: real DB/UI, explicitly mocked search and models."""
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests' / 'automated'))
from test_case_research import research_result, selection_result
from test_adaptive_tasks import bot_recipe
from da_agent.app import create_app
from da_agent.config import Settings


class FixtureProvider:
    def status(self):
        return {'state':'ready','provider':'fixture','model':'MOCK-search-and-generation'}

    def research(self, messages):
        return research_result()

    def select_case(self, messages):
        return selection_result('비정상 이용자 행동 조사 (모의 검색 검증)')

    def review(self, messages):
        payload = json.loads(messages[1]['content'])
        value = bot_recipe() if 'schema' in payload else {'aligned':True,'issues':[],
            'quality_dimensions':{key:'pass' for key in ('business_context','evidence_sufficiency',
                                                        'difficulty_fit','evaluation_alignment')}}
        return {'state':'completed','model':'MOCK-generation','text':json.dumps(value,ensure_ascii=False)}


if __name__ == '__main__':
    dsn = os.environ['RECORDS_DSN']
    schema = 'verify_request_' + uuid.uuid4().hex
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        with tempfile.TemporaryDirectory(prefix='case-ui-packages-') as root:
            os.environ['QUALITY_CALL_INTERVAL_SECONDS'] = '0'
            settings = Settings(packages_root=Path(root),records_dsn=make_conninfo(dsn,options=f'-c search_path={schema}'))
            uvicorn.run(create_app(settings,FixtureProvider()),host='0.0.0.0',port=8000,access_log=False)
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
