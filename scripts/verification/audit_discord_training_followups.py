"""Paired interpretation probes and post-review revision check, no Discord transport."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit,urlunsplit
from uuid import uuid4
from da_agent.discord_education import evaluate_report
from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.discord_query import DiscordQueryEngine
from da_agent.discord_store import DiscordStore
from da_agent.discord_service import DiscordTrainingService
from da_agent.sql_runner import SqlRunner

out=Path('/tmp/training-level-audit')
audit=json.loads((out/'audit.json').read_text())
doc=audit['levels']['advanced']['final']
provider=DiscordGemmaProvider(key_file='/run/secrets/gemma_key',model=os.environ['DISCORD_MODEL'])
original=provider.review
calls=[]
last=[time.monotonic()]
def review(messages,**kwargs):
    delay=max(0,16-(time.monotonic()-last[0]))
    if delay: time.sleep(delay)
    r=original(messages,**kwargs)
    last[0]=time.monotonic()
    calls.append({k:r[k] for k in ('state','reason','text','usage','model','quota_diagnostic') if k in r})
    return r
provider.review=review
result=dict(calls=calls,paired_probes=[])
report=doc['reports'][-1]
for name,bad in [('same_report_repeat',False),('same_report_false_composition',True)]:
    modified=json.loads(json.dumps(report))
    messages=json.loads(json.dumps(doc['messages']))
    if bad:
        old='1주차 채널 비율이 모두80%이므로 1주차 비율을 고정하면 유입 구성만 바꿔도 전체80%다. 따라서 구성 변화만으로 전체 하락을 설명하는 가설은 기각한다. 채널 내 관측 변화도 확인해야 한다.'
        new='1주차 채널 비율이 모두80%이지만 유입 구성만 바꾸면 전체70%가 된다. 채널 내 완료율 변화는 전체 하락에 기여하지 않는다. 따라서 구성 변화만으로 전체 하락을 완전히 설명한다.'
        assert old in modified['content']['report_text']
        modified['content']['report_text']=modified['content']['report_text'].replace(old,new)
        for message in messages:
            if old in message.get('text',''):
                message['text']=message['text'].replace(old,new)
    # Keep the report field, actual evidence, prior dialogue and followup identical.
    ev=evaluate_report(provider,doc['task'],modified,messages,doc['executions'],doc['help_history'])
    result['paired_probes'].append(dict(name=name,report=modified,result=ev))
    (out/'followups.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'probe':name,'total':ev['total'],'held':ev['held']},ensure_ascii=False),flush=True)
parts=urlsplit(os.environ['DISCORD_ADMIN_DSN'])
dsn=urlunsplit(parts._replace(path='/discord_audit_20261006'))
settings=SimpleNamespace(daily_call_limit=30)
service=DiscordTrainingService(DiscordStore(dsn),DiscordQueryEngine(provider,None,settings),provider,settings)
before=service.get_session(doc['owner_user_id'],doc['session_id'])
r=service.handle(doc['owner_user_id'],doc['session_id'],uuid4().hex,'report','평가 피드백을 반영해 보고서를 수정합니다.')
result['post_evaluation_revision']={'state_before':before['state'],'state_after':r['session']['state'],'reports_before':len(before['reports']),'reports_after':len(r['session']['reports']),'response':r['messages'],'errors':r['session']['telemetry'][-1]}
(out/'followups.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'revision':result['post_evaluation_revision']},ensure_ascii=False),flush=True)
