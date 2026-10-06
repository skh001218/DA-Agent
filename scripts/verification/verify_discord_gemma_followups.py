"""Bounded live clarification, equivalent-report grading and submission probes."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import uuid

import psycopg
from psycopg import sql

from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.discord_query import DiscordQueryEngine
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore
from da_agent.discord_education import evaluate_report
from da_agent.sql_runner import SqlRunner


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--key-file',required=True)
    p.add_argument('--output',required=True)
    args=p.parse_args()
    settings=SimpleNamespace(admin_dsn=os.environ['DISCORD_TEST_ADMIN_DSN'],learner_dsn=os.environ['DISCORD_TEST_LEARNER_DSN'],
        query_timeout_ms=5000,max_rows=1000,max_bytes=1048576,preview_rows=200,execution_ttl=600,daily_call_limit=6)
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(settings.admin_dsn)['dbname']=='discord_test'
    provider=DiscordGemmaProvider(key_file=args.key_file)
    store=DiscordStore(os.environ['DISCORD_TEST_RECORDS_DSN'])
    store.initialize()
    service=DiscordTrainingService(store,DiscordQueryEngine(provider,SqlRunner(settings),settings),provider,settings)
    owner=uuid.uuid4().hex
    session=service.start(owner,'test-guild','test-channel',uuid.uuid4().hex, practice='analysis')
    sid=session['session_id']
    data={'model':provider.model,'cases':[],'human_review':'pending'}
    review=provider.review
    data['provider_responses']=[]
    def trace(messages,**kwargs):
        response=review(messages,**kwargs)
        data['provider_responses'].append(response)
        return response
    provider.review=trace
    output=Path(args.output)
    def save(name,passed,details):
        data['cases'].append({'name':name,'passed':bool(passed),'details':details})
        output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'case':name,'passed':bool(passed)},ensure_ascii=False),flush=True)
    try:
        first=service.handle(owner,sid,uuid.uuid4().hex,'query','비율을 보여줘')
        time.sleep(16)
        answer='UTC 2026-09-01 포함~2026-09-15 제외 가입 고유 사용자가 분모, 같은 기간 튜토리얼3단계 완료 고유 사용자가 분자이고 채널별 비율이야. 중복 완료를 사용자별로 제거해.'
        second=service.handle(owner,sid,uuid.uuid4().hex,'query',answer)
        restored=service.resume(owner,'test-guild',sid)['session']
        passed=bool(first['session']['pending_query']) and not first['session']['executions'] and bool(restored['executions']) and restored['queries'][-1]['original_text']=='비율을 보여줘' and restored['queries'][-1]['user_answer']==answer
        save('persisted_live_clarification',passed,{'queries':restored['queries'],'executions':restored['executions'],'help_history':restored['help_history']})
        if restored['executions']:
            evidence=' 실제 실행 근거: '+json.dumps(restored['executions'][0]['result']['rows'],ensure_ascii=False)
            texts=[
                '목표는 신규 가입자의 튜토리얼3단계 완료율 변화와 우선 대응이다. UTC9/1~9/15 가입 고유 사용자를 분모로 같은 기간 완료 고유 사용자를 분자로 사용했다. 재도전 중복을 점검하고 사용자별로 제거했다. 공개 데이터만으로 실제 수집 누락은 확인 못한다. 난이도 가설을 검토하되 채널 구성 변화가 대안이며 채널별 차이가 없다면 가설이 약화된다. 관측 차이를 인과로 확정하지 않고 판단 보류한다. 먼저 수집 상태·유입 구성을 확인한 후 무작위 안내 개선 실험을 수행하고 완료율과 로그 완전성을 관찰한다.',
                '분석 질문: 신규 가입자의 튜토리얼3단계 완료율 변화에 어떻게 대응할까? 계산: UTC9/1~9/15 가입 고유 사용자 중 같은 기간 완료한 고유 사용자 비율. 품질: 재도전을 사용자별로 중복 제거했고 실제 누락 여부는 이 공개 자료만으로 확인 불가. 가설: 난이도 문제, 대안: 채널 구성 변화. 반박 조건: 채널별 차이가 없으면 난이도 가설 약화. 결론: 관측 연관성만 확인 가능해 인과는 판단 보류. 대응: 먼저 수집 상태와 유입 구성 확인, 이후 무작위 안내 개선 실험. 후속 지표: 완료율과 로그 완전성.'
            ]
            results=[]
            for text in texts:
                time.sleep(16)
                results.append(evaluate_report(provider,session['task'],{'version':1,'text':text+evidence},[],restored['executions'],[]))
            grades=[{c['id']:c['grade'] for c in r['criteria']} for r in results]
            save('equivalent_report_style',all(not r['held'] for r in results) and grades[0]==grades[1],{'evaluations':results,'grades':grades})
            service.handle(owner,sid,uuid.uuid4().hex,'evidence',payload={'execution_id':restored['executions'][0]['execution_id']})
            service.handle(owner,sid,uuid.uuid4().hex,'report',texts[0]+evidence)
            service.handle(owner,sid,uuid.uuid4().hex,'followup','수집 로그 검증을 먼저 하고 무작위 안내 개선 실험으로 완료율과 로그 완전성을 확인한다.')
            time.sleep(16)
            final=service.handle(owner,sid,uuid.uuid4().hex,'submit')
            save('live_service_roundtrip',final['session']['state']=='completed',{'evaluations':final['session']['evaluations'],'telemetry':final['session']['telemetry']})
    finally:
        with psycopg.connect(settings.admin_dsn) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(session['schema_name'])))


if __name__=='__main__': main()
