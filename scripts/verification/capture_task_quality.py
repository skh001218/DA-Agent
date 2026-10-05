"""Capture explicit synthetic training requests and raw-free usage for comparison."""
import argparse
import json
from pathlib import Path
import httpx
from da_agent.config import Settings
from da_agent.store import Store

parser=argparse.ArgumentParser()
parser.add_argument('request_ids',nargs='+')
parser.add_argument('--output',required=True)
args=parser.parse_args()
settings=Settings()
store=Store(settings.records_dsn)
records=[]
with store.connect() as conn, httpx.Client(base_url='http://127.0.0.1:8000') as client:
    for rid in args.request_ids:
        request=client.get('/api/training/requests/'+rid).json()
        value={'request':request}
        job=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()
        if job:
            fixed=job['private']
            value.update(schema_reviews=fixed.get('schema_reviews',[]),alignment_reviews=fixed.get('alignment_reviews',[]))
            recipe=fixed.get('adaptive_recipe')
            if recipe:
                value['recipe']=recipe
        value['usage']=[r['payload'] for r in conn.execute("SELECT payload FROM quality_operations WHERE payload->>'request_id'=%s AND payload->>'kind'='ai' ORDER BY payload->>'started_at'",(rid,))]
        if request.get('attempt_id'):
            attempt=client.get('/api/attempts/'+request['attempt_id']).json()
            value['attempt']=attempt
            root=settings.packages_root/attempt['package_id']/'v1'
            reference=root/'private'/'reference.json'
            if reference.exists(): value['private_reference']=json.loads(reference.read_text())
        records.append(value)
Path(args.output).write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{'request_id':v['request']['request_id'],'status':v['request']['status'],'calls':v['request']['planning_calls']} for v in records],ensure_ascii=False))
