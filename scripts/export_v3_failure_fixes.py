"""Export states and grades only; preserve raw historical runs in the recorder."""
import copy
import json
import os
from pathlib import Path
from da_agent.store import Store
from da_agent.evaluation_v3 import normalize
from da_agent.quality_v2 import summarize, automatic_verdict

store=Store(os.environ['RECORDS_DSN'])
ids=['6f10c92e-5b35-4f58-b1de-3826f30114f4','2b1b9e9d-00bd-4c3a-8ff4-c10f36d06f41',
     '7ce868bd-4c55-4ceb-aacc-dd01f9ac094d','634f0ecd-29d6-4a88-a1b5-eb64de68a2cb']
out=[]
with store.connect() as conn:
    for run_id in ids:
        run=conn.execute('SELECT payload FROM quality_runs WHERE run_id=%s',(run_id,)).fetchone()['payload']
        private=conn.execute('SELECT private FROM training_plans WHERE attempt_id=%s',(run['attempt_id'],)).fetchone()['private']
        derived=copy.deepcopy(run)
        if run_id==ids[1]:
            for sample in derived['samples']:
                for result in sample['results']:
                    feedback=result['feedback']
                    wire={k:feedback[k] for k in ('strengths','improvements','next_steps','uncertainty')}
                    wire['criteria']=[dict(key=r['key'],conditions=[dict(id=c['id'],state=c['state'],reason=c['reason'],sources=[dict(path=s['path']) for s in c['sources']]) for c in r['conditions']]) for r in feedback['criteria']]
                    new=normalize(dict(status='completed',feedback=wire),sample['report'],sample['evidence'],private['frozen_evaluation'],private)
                    result.update(new)
                    result['automatic_verdict']=automatic_verdict(sample,result)
        summary=summarize(derived)
        original_summary=summarize(run)
        out.append(dict(run_id=run_id,replay_with_latest_server=run_id==ids[1],ai_calls_for_replay=0,
            original_verdict=original_summary['verdict'],
            original_variation={s['id']:dict(score_range=s['score_range'],level_variation=s['level_variation'],critical_state_variation=s['critical_state_variation']) for s in original_summary['samples']},
            actual_model=True,actual_db=True,status=summary['status'],verdict=summary['verdict'],human_quality='pending',
            calls=summary['completed_calls'],samples=[dict(id=s['id'],verdict=s['verdict'],score_range=s['score_range'],
            level_variation=s['level_variation'],critical_state_variation=s['critical_state_variation'],
            repetitions=[dict(status=r['status'],error=r.get('error'),automatic_verdict=r['automatic_verdict'],
             levels={c['key']:c['level'] for c in (r.get('feedback') or {}).get('criteria',[])},
             conditions={c['key']:{x['id']:x['state'] for x in c['conditions']} for c in (r.get('feedback') or {}).get('criteria',[])})
             for r in s['results']]) for s in summary['samples']]))
print(json.dumps(dict(runs=out,original_raw_runs_preserved=True),ensure_ascii=False))
