"""Metered real-model boundary checks using retained verification data only."""
import copy
import json
import os
import threading
import uuid
import sys
from types import SimpleNamespace
from psycopg.types.json import Jsonb
from da_agent.store import Store, now
from da_agent.api_provider import configured_provider
from da_agent import quality_v2 as quality

store = Store(os.environ['RECORDS_DSN'])
with store.connect() as conn:
    original = conn.execute('SELECT payload FROM quality_runs WHERE run_id=%s',
                            ('6f10c92e-5b35-4f58-b1de-3826f30114f4',)).fetchone()['payload']
    plan = conn.execute('SELECT public,private FROM training_plans WHERE attempt_id=%s',
                        (original['attempt_id'],)).fetchone()

correct = next(s for s in original['samples'] if s['id'] == 'correct')
wrong = next(s for s in original['samples'] if s['id'] == 'core_error')
samples = []
for key in (sys.argv[1:] or ('valid_plan_wrong_execution', 'withdrawn_causal_claim', 'plan_in_other_section')):
    sample = copy.deepcopy(correct)
    sample.update(id=key, title=key, results=[], expected_levels={k:[3,4] for k in original['samples'][0]['expected_levels']})
    if key == 'valid_plan_wrong_execution':
        sample['evidence'] = copy.deepcopy(wrong['evidence'])
        sample['report']['claims'] = copy.deepcopy(wrong['report']['claims'])
        sample['report']['claims'][0]['text'] = '현재 저장 실행으로 고유 유저 계산을 검증하지 못했다. 원인은 확정할 수 없다.'
        sample['report']['content']['interpretation'] = '현재 실행의 JOIN 행 수를 고유 유저 수로 해석할 수 없다. 계산을 수정하고 검증하기 전에는 결론을 보류한다.'
        sample['expected_levels'].pop('sql_accuracy')
        sample['expected_statuses'] = {'sql_accuracy':'held'}
    elif key == 'withdrawn_causal_claim':
        sample['report']['content']['interpretation'] = ('이전에 관측 차이가 특정 원인을 증명한다고 주장했으나 그 주장을 철회한다. '
            '최종 결론은 저장 실행에서 확인한 관측 결과에 한정하고 원인을 확정하지 않는다. 대안 설명에는 별도 비교가 필요하다.')
    elif key == 'plan_in_other_section':
        sample['report']['content']['report_text'] = sample['report']['content'].pop('hypothesis')
    elif key == 'missing_evidence':
        sample = copy.deepcopy(next(s for s in original['samples'] if s['id']=='missing_evidence'))
        sample.update(results=[], expected_levels={'problem_definition':[0,0], 'analysis_approach':[0,0],
                     'sql_accuracy':[0,0], 'interpretation':[1,1], 'next_actions':[0,0]})
    samples.append(sample)

run = dict(original, run_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), status='running',
           started_at=now(), samples=samples, semantic_approval=False)
run.pop('finished_at', None)
with store.connect() as conn:
    conn.execute('INSERT INTO quality_runs VALUES(%s,%s)', (run['run_id'],Jsonb(run)))

class Package:
    def problem(self, _): return plan['public']
    def reference(self, _): return plan['private']

def append(sample_id, result):
    next(s for s in run['samples'] if s['id']==sample_id)['results'].append(result)
    with store.connect() as conn:
        conn.execute('UPDATE quality_runs SET payload=%s WHERE run_id=%s',(Jsonb(run),run['run_id']))

training = SimpleNamespace(store=store, auth=configured_provider(), ai_lock=threading.Lock())
quality.evaluate(training, Package(), run, append)
run.update(status='completed', finished_at=now())
with store.connect() as conn:
    conn.execute('UPDATE quality_runs SET payload=%s WHERE run_id=%s',(Jsonb(run),run['run_id']))
summary=quality.summarize(run)
print(json.dumps(dict(run_id=run['run_id'],actual_model=True,actual_db=True,
    completed_calls=summary['completed_calls'],verdict=summary['verdict'],human_quality='pending',
    samples=[dict(id=s['id'],verdict=s['verdict'],critical_state_variation=s['critical_state_variation'],
      repetitions=[dict(status=r['status'],error=r.get('error'),automatic_verdict=r['automatic_verdict'],
          levels={c['key']:c['level'] for c in (r.get('feedback') or {}).get('criteria',[])}) for r in s['results']])
          for s in summary['samples']]),ensure_ascii=False))
