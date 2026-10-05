"""Probe only synthetic review inputs; never write keys or real reports."""
import json
from da_agent.api_provider import configured_provider
from da_agent.evaluation import freeze_evaluation
from da_agent.evaluation_v3 import review_messages, review_envelope

provider=configured_provider()
original=provider.http
captured=[]
mode='original'
class Probe:
    def post(self,*args,**kwargs):
        config=kwargs['json']['generationConfig']
        if mode=='tokens4096':config['maxOutputTokens']=4096
        if mode=='simple':config['responseJsonSchema']={'type':'object','properties':{'decision':{'type':'string'}},'required':['decision']}
        if mode=='unbounded':
            def strip(value):
                if isinstance(value,dict):return {k:strip(v) for k,v in value.items() if k not in ('maxItems','minItems','maxLength','minLength')}
                if isinstance(value,list):return [strip(v) for v in value]
                return value
            config['responseJsonSchema']=strip(config['responseJsonSchema'])
        response=original.post(*args,**kwargs)
        if response.status_code!=200:
            error=response.json().get('error',{})
            captured.append(dict(status=response.status_code,code=error.get('status'),message=error.get('message','')[:2000]))
        return response
provider.http=Probe()
for mode in ('tokens4096','simple','unbounded'):
    count=1
    weights={'analysis_approach':100} if count==1 else {'problem_definition':25,'analysis_approach':25,'sql_accuracy':20,'interpretation':20,'next_actions':10}
    p=dict(task_kind='design' if count==1 else 'calculation',evaluation_version='request-review-v3',weights=weights,completion_conditions=['대상·기간·고유 유저 정의와 확인 방법 설명'])
    f=freeze_evaluation(p,{'weights':weights})
    r={'content':{'problem_definition':'고유 유저 수를 기간별로 확인한다.'},'claims':[]}
    response=provider.review(review_messages(review_envelope(p,r,[],f)))
    print(json.dumps(dict(probe_mode=mode,criteria_count=count,state=response.get('state'),reason=response.get('reason'),provider_errors=captured),ensure_ascii=False))
    captured.clear()
