"""Read only the explicitly named Gemma verification requests and their artifacts."""
import json
from pathlib import Path
import sys

from da_agent.config import Settings
from da_agent.store import Store


settings=Settings()
samples=[]
with Store(settings.records_dsn).connect() as conn:
    for rid in sys.argv[1:]:
        row=conn.execute('SELECT r.payload,j.private FROM training_requests r JOIN generation_jobs j USING(request_id) WHERE r.request_id=%s',(rid,)).fetchone()
        sample={'request':row['payload'],'diagnostics':row['private'],
                'operations':[r['payload'] for r in conn.execute("SELECT payload FROM quality_operations WHERE payload->>'request_id'=%s",(rid,))]}
        aid=row['payload'].get('attempt_id')
        if aid:
            attempt=conn.execute('SELECT payload FROM attempts WHERE attempt_id=%s',(aid,)).fetchone()['payload']
            sample['attempt']=attempt
            sample['plan']=conn.execute('SELECT public,private FROM training_plans WHERE attempt_id=%s',(aid,)).fetchone()
            reference=settings.packages_root/attempt['package_id']/'v1'/'private'/'reference.json'
            if reference.is_file():sample['reference']=json.loads(reference.read_text())
        samples.append(sample)
Path('/tmp/gemma-generation-audit.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2,default=str))
print(json.dumps([{'request_id':s['request']['request_id'],'status':s['request']['status'],
                  'error':s['request'].get('error'),'topic':s['diagnostics'].get('source_case',{}).get('topic'),
                  'schema_issues':s['diagnostics'].get('schema_issues'),
                  'alignment_reviews':s['diagnostics'].get('alignment_reviews')} for s in samples],ensure_ascii=False))
