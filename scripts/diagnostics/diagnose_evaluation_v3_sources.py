"""Inspect reference failure kinds without recording source text or quotes."""
import json
import os
import urllib.request
import psycopg
from psycopg.rows import dict_row
from da_agent.api_provider import configured_provider
from da_agent.evaluation_v3 import review_envelope, review_messages, source_texts

run=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/quality/v2/runs/95761c2d-5a0f-41c9-8f49-b16ae245be92'))
sample=next(s for s in run['samples'] if s['id']=='valid_alternative')
with psycopg.connect(os.environ['RECORDS_DSN'],row_factory=dict_row) as conn:
    plan=conn.execute('SELECT public,private FROM training_plans WHERE attempt_id=%s',(run['attempt_id'],)).fetchone()
result=configured_provider().review(review_messages(review_envelope(plan['public'],sample['report'],sample['evidence'],plan['private']['frozen_evaluation'])))
value=json.loads(result['text']);sources=source_texts(sample['report'],sample['evidence']);issues=[]
for row in value['criteria']:
    for c in row['conditions']:
        for ref in c['sources']:
            if ref['path'] not in sources:issues.append(dict(criterion=row['key'],condition=c['id'],kind='unknown_path'))
            elif 'quote' in ref:issues.append(dict(criterion=row['key'],condition=c['id'],kind='unsupported_model_quote'))
        if c['state'] in ('met','error') and not c['sources'] and not (c['id']=='critical' and c['state']=='met') and not (row['key']=='sql_accuracy' and c['id']!='advanced'):
            issues.append(dict(criterion=row['key'],condition=c['id'],kind='missing_source'))
print(json.dumps(dict(actual_model=True,sample_id=sample['id'],issues=issues),ensure_ascii=False))
