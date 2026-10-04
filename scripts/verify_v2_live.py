"""Explicit real-model smoke test. Create only verification records; never reset DB."""
import argparse
import json
import time
import uuid
import urllib.request
from pathlib import Path
from da_agent.packages import PackageCatalog

def run(base,output):
    def api(path,body=None,method=None):
        request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,method=method,
                                     headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=120) as response: return json.load(response)
    result={'actual_model':True,'actual_db':True,'checks':{},'human_quality':'pending'}
    request_id='live-v2-'+uuid.uuid4().hex
    api('/api/training/requests',{'contract_version':'request-v2','request_id':request_id,'message':'접속 데이터에서 관측이 끝난 신규 유저의 D1~D7 미재접속률을 정확히 계산하는 초급 훈련을 하고 싶어요.','difficulty':'beginner','task_kind':'calculation','data_mode':'existing'})
    for _ in range(240):
        value=api('/api/training/requests/'+request_id)
        if value['status'] in ('ready','failed','needs_clarification','interrupted'): break
        time.sleep(.5)
    result['request_status']=value['status']
    if value['status']!='ready':
        result['error_code']=value.get('error_code')
    else:
        aid=value['attempt_id'];prefix='/api/attempts/'+aid
        attempt=api(prefix)
        result['attempt_id']=aid
        package=PackageCatalog('packages').load(attempt['package_id'],attempt['release_version'])
        reference=package.reference(attempt['problem_id'])
        execution=api(prefix+'/execute',{'sql':reference['sql']})
        result['checks']['sql_success']=execution['status']=='success' and execution['result_complete']
        if result['checks']['sql_success']:
            saved=api(prefix+'/executions/save',{'request_id':uuid.uuid4().hex,'execution_id':execution['execution_id']})
            observed=dict(zip([c['name'] for c in saved['result']['columns']],saved['result']['rows'][0]))
            claims=[{'claim_id':'computed','text':json.dumps(observed,ensure_ascii=False),'evidence_refs':[{'saved_execution_id':saved['saved_execution_id'],'rows':[0],'columns':list(observed)}]}]
            content={'problem_definition':'Asia/Seoul 기준 가입 코호트, D1 00:00 포함~D8 00:00 제외. D8까지 관측된 유저만 분모에 포함하고 고유 유저 단위로 셉니다.',
                     'hypothesis':'관측 완료 여부를 먼저 확인하고 기간 내 재접속 유무를 사용자별 계산합니다.',
                     'report_text':json.dumps(observed,ensure_ascii=False),'interpretation':'이 미재접속 비율은 지정 관측 기간의 현상입니다. 플랫폼이나 버전의 원인 효과는 증명되지 않았습니다.',
                     'limitations':'미관측 기간은 제외했습니다. 원인·다른 코호트로 일반화할 수 없습니다.','next_actions':'수집 경계와 다른 가입 기간에서 같은 정의의 지표를 비교합니다.'}
            report=api(prefix+'/reports',{'revision':0,'request_id':uuid.uuid4().hex,'content':content,'claims':claims})
            review=api(prefix+'/reports/'+report['report_id']+'/review',{'request_id':uuid.uuid4().hex})
            result['checks']['review_status']=review['status']
            result['review_error_code']=(review.get('error') or {}).get('code')
            result['model']=review.get('model')
            result['rules_version']=review.get('rules_version')
            result['checks']['resume_saved']=len(api(prefix)['saved_executions'])==1
        # Remove exactly this test's records, preserving all user/shared packages.
        result['checks']['delete_own_record']=api(prefix,{},'DELETE')['deleted']
    result['request_id']=request_id
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8087')
    parser.add_argument('--output',type=Path,default=Path('tests/verification-v2-live-2026-10-04.json'))
    args=parser.parse_args()
    run(args.base_url,args.output)
