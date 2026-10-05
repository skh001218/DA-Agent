"""Recalculate persisted condition states with no model calls or raw logs."""
import json
import os
import psycopg
from psycopg.rows import dict_row
from da_agent.evaluation_v3 import normalize

with psycopg.connect(os.environ['RECORDS_DSN'],row_factory=dict_row) as conn:
    run=conn.execute('SELECT payload FROM quality_runs WHERE run_id=%s',('edb0229d-b6f4-45ae-9b3d-7525a01f8cda',)).fetchone()['payload']
    private=conn.execute('SELECT private FROM training_plans WHERE attempt_id=%s',(run['attempt_id'],)).fetchone()['private']
checks=[]
for sample in run['samples']:
    for result in sample['results']:
        original=result['feedback']
        wire={k:original[k] for k in ('strengths','improvements','next_steps','uncertainty')}
        wire['criteria']=[{'key':r['key'],'conditions':[dict(id=c['id'],state=c['state'],reason=c['reason'],sources=[dict(path=s['path']) for s in c['sources']]) for c in r['conditions']]} for r in original['criteria']]
        output=normalize({'status':'completed','feedback':wire},sample['report'],sample['evidence'],private['frozen_evaluation'],private)
        equal=output['status']=='completed' and output['feedback']['total_score']==original['total_score'] and [c['level'] for c in output['feedback']['criteria']]==[c['level'] for c in original['criteria']]
        checks.append(dict(sample=sample['id'],repetition=result['repetition'],same_grades_and_score=equal))
print(json.dumps(dict(run_id=run['run_id'],ai_calls=0,passed=sum(c['same_grades_and_score'] for c in checks),total=len(checks),checks=checks)))
assert all(c['same_grades_and_score'] for c in checks)
