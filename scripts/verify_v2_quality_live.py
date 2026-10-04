"""Run an explicit operator suite (18 real model calls), retain all repetitions."""
import argparse,json,time,uuid,urllib.request
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('attempt_id')
    parser.add_argument('--mode',choices=('review','coaching'),default='review')
    parser.add_argument('--base-url',default='http://127.0.0.1:8087')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    def api(path,body=None):
        request=urllib.request.Request(args.base_url+path,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)
    started=api('/api/quality/v2/runs',{'request_id':'quality-live-'+uuid.uuid4().hex,'attempt_id':args.attempt_id,'mode':args.mode})
    print(json.dumps({'run_id':started['run_id'],'planned_calls':18}),flush=True)
    previous=-1
    deadline=time.monotonic()+1800
    while time.monotonic()<deadline:
        run=api('/api/quality/v2/runs/'+started['run_id'])
        if run['completed_calls']!=previous:
            previous=run['completed_calls']
            print(json.dumps({'completed_calls':previous,'status':run['status']}),flush=True)
        if run['status']!='running':break
        time.sleep(1)
    summary={k:run[k] for k in ('run_id','attempt_id','mode','status','configured_model','provider','fixture_version','evaluation_version','completed_calls','verdict','semantic_approval')}
    summary.update(actual_model=True,actual_db=True,human_quality='pending',samples=[{'id':sample['id'],'score_range':sample['score_range'],'verdict':sample['verdict'],'repetitions':[{'repetition':r['repetition'],'status':r['status'],'automatic_verdict':r['automatic_verdict'],'error_code':(r.get('error') or {}).get('code'),'model':r.get('model')} for r in sample['results']]} for sample in run['samples']])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ('status','completed_calls','verdict','semantic_approval')}),flush=True)
    return 0 if run['status']=='completed' and run['verdict']!='fail' else 1

if __name__=='__main__':raise SystemExit(main())
