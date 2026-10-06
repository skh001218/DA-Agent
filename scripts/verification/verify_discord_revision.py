"""Spec032 live Gemma/PostgreSQL flow; isolated DB, no Discord sends."""
from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path
import time
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from da_agent.discord_education import prepare_dataset
from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.discord_query import DiscordQueryEngine
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore
from da_agent.sql_runner import SqlRunner


def main():
    out = Path(os.getenv('SPEC032_OUTPUT', '/tmp/spec032/output'))
    out.mkdir(parents=True, exist_ok=True)
    def dsn(key):
        return urlunsplit(urlsplit(os.environ[key])._replace(path='/discord_test'))
    settings = SimpleNamespace(admin_dsn=dsn('DISCORD_ADMIN_DSN'), learner_dsn=dsn('DISCORD_LEARNER_DSN'),
        query_timeout_ms=5000, max_rows=1000, max_bytes=1048576, preview_rows=10, execution_ttl=600, daily_call_limit=12)
    store = DiscordStore(settings.admin_dsn)
    store.initialize()
    provider = DiscordGemmaProvider(key_file='/run/secrets/gemma_key', model=os.environ['DISCORD_MODEL'])
    original, calls, last = provider.review, [], [0.0]
    def review(messages, **kwargs):
        if len(calls) >= 8: return {'state':'error','reason':'verification_call_limit'}
        delay = max(0,16-(time.monotonic()-last[0]))
        if delay: time.sleep(delay)
        result = original(messages, **kwargs)
        last[0] = time.monotonic()
        calls.append({k: result[k] for k in ('state','reason','usage','model','text') if k in result})
        print(json.dumps({'call':len(calls),'state':result.get('state'),'reason':result.get('reason')}, ensure_ascii=False), flush=True)
        return result
    provider.review = review
    service = DiscordTrainingService(store, DiscordQueryEngine(provider,SqlRunner(settings),settings),provider,settings)
    owner, event = 'spec032-'+uuid4().hex, lambda:uuid4().hex
    doc = service.start(owner,'verification','verification',event(),difficulty='advanced', practice='analysis')
    sid = doc['session_id']
    transcript=[]
    def handle(action,text='',payload=None):
        response=service.handle(owner,sid,event(),action,text,payload or {})
        transcript.append(dict(action=action,messages=response['messages']))
        print(json.dumps({'action':action,'state':response['session']['state']},ensure_ascii=False),flush=True)
        return response
    try:
        for start,end in [('2026-09-01','2026-09-08'),('2026-09-08','2026-09-15')]:
            text=f'UTC {start} 포함부터 {end} 제외까지 가입한 신규 고유 사용자 전체를 분모로, 이들 중 UTC 2026-09-01 포함부터 2026-10-01 제외까지 튜토리얼 3단계를 완료한 고유 사용자를 분자로 완료율을 유입 채널별로 조회해줘. 사용자 단위이고 재도전 중복은 제거하며 다른 필터는 없어.'
            response=handle('query',text)
            if response['session'].get('pending_query'): response=handle('answer',text)
        doc=store.get(owner,sid)
        assert len(doc['executions'])==2, 'Natural language queries did not both succeed'
        for execution in doc['executions']:
            handle('evidence',payload={'execution_id':execution['execution_id']})
        bad=('업무 목표는 두 주 신규 가입자의 튜토리얼3단계 완료율 변화를 확인하고 우선 대응을 정하는 것이다. '
             'UTC 가입 기간은9월1~7일 및8~14일이고 완료 관측은10월1일 제외까지다. 분모는 신규 가입 고유 사용자, 분자는3단계 완료 고유 사용자다. '
             '1주차16/20=80%. 2주차14/20=70%. 전체 완료율은10%p 하락했다. '
             '1주차 채널 비율이 모두80%이지만 유입 구성만 바꾸면 전체70%가 된다. '
             '채널 내 완료율 변화는 전체 하락에 기여하지 않는다. 구성 변화만으로 전체 하락을 완전히 설명한다. '
             '관측은 인과를 증명하지 않는다. 재도전은 고유 사용자로 제거했고 실제 로그 누락은 확인 불가다. 표본40명으로 추가 로그와 표본 확인을 먼저 제안한다.')
        handle('report',bad)
        handle('followup','현재 자료로 인과는 확정하지 않고 수집 상태와 기기/버전 로그를 먼저 확인한다.')
        first=handle('submit')
        one=deepcopy(first['session']['evaluations'][-1])
        assert one['result']['arithmetic_verification']['errors']
        assert one['result']['held'] or one['result']['total']<100
        old_report=deepcopy(first['session']['reports'][0])
        correct=bad.replace('1주차 채널 비율이 모두80%이지만 유입 구성만 바꾸면 전체70%가 된다.',
                            '1주차 채널 비율을 고정하고 구성만 바꾸면 전체80%가 된다.').replace(
            '채널 내 완료율 변화는 전체 하락에 기여하지 않는다. 구성 변화만으로 전체 하락을 완전히 설명한다.',
            'organic은80%로 유지하고 ads는80%에서66.67%로 낮아졌다. 구성 변화만으로 전체 하락을 설명할 수 없다. ads 집단의 관측 변화도 확인해야 한다.')
        handle('report',correct)
        blocked=handle('submit')
        assert len(blocked['session']['evaluations'])==1
        assert '후속 질문' in blocked['messages'][0]
        handle('followup','추가 로그와 충분한 표본을 확인하고 필요하면 무작위 실험으로 완료율 및 D7·오류율을 검증한다.')
        second=handle('submit')
        final=second['session']
        assert final['reports'][0]==old_report and final['evaluations'][0]==one
        assert len(final['reports'])==2 and len(final['evaluations'])==2
        assert not final['evaluations'][1]['result']['arithmetic_verification']['errors']
        if final['state']=='completed':
            count=len(calls); handle('submit'); assert count==len(calls)
        restarted=DiscordTrainingService(DiscordStore(settings.admin_dsn),service.engine,provider,settings)
        restored=restarted.resume(owner,'verification',sid)['session']
        assert restored['reports']==final['reports'] and restored['evaluations']==final['evaluations']
        data=dict(model=provider.model,calls=calls,transcript=transcript,session=final,
                  first_submission=first.get('submission'),second_submission=second.get('submission'),
                  restored=True,discord_transport='not_sent')
        import da_agent
        package = Path(da_agent.__file__).parent
        data['code_hashes'] = {name:hashlib.sha256((package/name).read_bytes()).hexdigest()
                               for name in ('discord_verification.py','discord_education.py','discord_service.py','discord_results.py','discord_presentation.py')}
        (out/'live.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'first':one['result']['total'],'second':final['evaluations'][1]['result']['total'],
                          'first_held':one['result']['held'],'second_held':final['evaluations'][1]['result']['held'],'restored':True},ensure_ascii=False),flush=True)
    finally:
        with psycopg.connect(settings.admin_dsn) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(doc['schema_name'])))
        (out/'calls.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
