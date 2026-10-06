"""Read-only audit of explicitly requested synthetic difficulty samples."""
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg import sql
from da_agent.config import Settings
from da_agent.store import Store

settings = Settings()
target = '실무에서 일어날만한 문제를 분석하고싶어'
output = {'request_text': target, 'runtime_hashes': {}, 'samples': []}
for name in ('adaptive_tasks.py', 'task_quality.py', 'task_generation.py'):
    output['runtime_hashes'][name] = hashlib.sha256(Path('/app/src/da_agent', name).read_bytes()).hexdigest()
with Store(settings.records_dsn).connect() as conn:
    records = conn.execute('SELECT r.payload,j.private FROM training_requests r JOIN generation_jobs j USING(request_id)').fetchall()
    for record in records:
        request, private = record['payload'], record['private']
        if request.get('body', {}).get('message') != target:
            continue
        item = {'request': request, 'diagnostics': {k: private.get(k) for k in ('schema_reviews', 'schema_issues', 'alignment_reviews', 'adaptive_recipe', 'seed', 'package_id')}}
        item['usage'] = [r['payload'] for r in conn.execute("SELECT payload FROM quality_operations WHERE payload->>'request_id'=%s AND payload->>'kind'='ai'", (request['request_id'],))]
        if request.get('attempt_id'):
            attempt = conn.execute('SELECT payload FROM attempts WHERE attempt_id=%s', (request['attempt_id'],)).fetchone()['payload']
            item['attempt'] = attempt
            root = settings.packages_root / attempt['package_id'] / 'v1'
            item['reference'] = json.loads((root / 'private' / 'reference.json').read_text())
            recipe = private['adaptive_recipe']
            tables = {t['name']: t for t in recipe['tables']}
            base = next((t for t in recipe['tables'] if any(c['name'] == 'success' for c in t['columns'])), None)
            if base:
                with psycopg.connect(settings.learner_dsn) as learner:
                    schema = 'pkg_' + hashlib.sha256((attempt['package_id'] + '/v1').encode()).hexdigest()[:20]
                    learner.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
                    count = learner.execute(sql.SQL('SELECT count(*),count(DISTINCT account_id),avg(success::float)*100,avg(duration_seconds) FROM {}').format(sql.Identifier(base['name']))).fetchone()
                    item['observed_summary'] = dict(zip(('attempts', 'accounts', 'success_pct', 'mean_duration'), count))
                    profile = tables.get('profiles')
                    if profile:
                        dimensions = [c['name'] for c in profile['columns'] if c['name'] != 'id']
                        columns = sql.SQL(', ').join(sql.Identifier('p', d) for d in dimensions)
                        query = sql.SQL('SELECT {},count(*),avg(a.success::float)*100,avg(a.duration_seconds) FROM {} a JOIN profiles p ON a.account_id=p.id GROUP BY {} ORDER BY {}').format(columns, sql.Identifier(base['name']), columns, columns)
                        item['joint_profile_comparison'] = {'columns': dimensions + ['attempts', 'success_pct', 'mean_duration'], 'rows': learner.execute(query).fetchall()}
        output['samples'].append(item)
output['samples'].sort(key=lambda x: x['request']['created_at'])
print(json.dumps(output, ensure_ascii=False, default=str))
