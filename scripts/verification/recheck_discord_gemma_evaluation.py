"""Validate recorded real responses, then retry one live submission in isolated DB."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid
import time

from da_agent.discord_education import representative_task, evaluate_report
from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.discord_query import DiscordQueryEngine
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore
from da_agent.sql_runner import SqlRunner


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--key-file',required=True)
    p.add_argument('--artifact',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--positive-only',action='store_true')
    args=p.parse_args()
    source=json.loads(Path(args.artifact).read_text(encoding='utf-8'))
    executions=source['cases'][0]['details']['executions']
    evaluations=[]
    for response in source['provider_responses'][2:]:
        provider=SimpleNamespace(review=lambda messages,response=response: response)
        evaluations.append(evaluate_report(provider,representative_task(),{'version':1,'text':'recorded synthetic report'},[],executions,[]))
    grades=[{c['id']:c['grade'] for c in result['criteria']} for result in evaluations]
    results=[{'name':'real_response_style_revalidation','passed':len(evaluations)>=2 and all(not r['held'] for r in evaluations[:2]) and grades[0]==grades[1],
        'details':{'evaluations':evaluations,'grades':grades,'source':'recorded real Gemma response unchanged; parser revalidation only'}}]
    store=DiscordStore(os.environ['DISCORD_TEST_RECORDS_DSN'])
    target=executions[0]['execution_id']
    with store.connect() as conn:
        documents=conn.execute('SELECT document FROM discord_records.sessions WHERE guild_id=%s',('test-guild',)).fetchall()
    session=next(doc[0] for doc in documents if any(e['execution_id']==target for e in doc[0]['executions']))
    settings=SimpleNamespace(learner_dsn=os.environ['DISCORD_TEST_LEARNER_DSN'],query_timeout_ms=5000,max_rows=1000,
        max_bytes=1048576,preview_rows=200,execution_ttl=600,daily_call_limit=6)
    provider=DiscordGemmaProvider(key_file=args.key_file)
    live_calls=[0]
    original_review=provider.review
    def tracked_review(messages,**kwargs):
        live_calls[0]+=1
        return original_review(messages,**kwargs)
    provider.review=tracked_review
    if args.positive_only:
        results=[]
        texts=[
            ('valid_uncertainty','업무 목표는 신규 튜토리얼3단계 완료율 변화와 우선 대응이다. UTC 9/1~9/15 가입자 분모, 해당 기간 단계3 완료 고유 사용자 분자다. 재도전과 사용자 단위를 구분하고 채널 구성 변화 대안을 검토했다. 관측 차이는 인과 증거가 아니며 로그 누락은 공개 데이터만으로 확인 불가다. 게임 난이도 가설은 채널별 완료율이 비슷하면 약화될 수 있어 비교하고 필요하면 기각한다. 먼저 수집 상태와 유입 구성 확인, 이후 무작위 실험으로 튜토리얼 안내를 개선하고 고유 사용자 완료율과 로그 완전성을 관찰한다.'),
            ('valid_hypothesis_revision','초기 가설은 튜토리얼 난이도였다. UTC9/1~9/15 가입 고유 사용자 중 같은 기간 단계3 완료 고유 사용자 비율을 채널별로 비교하고 재도전을 중복 제거했다. 채널 구성 대안을 검토하고 난이도를 유일 원인으로 확정하지 않기로 수정했다. 공개 수집 정보만으로 누락은 확인 못해 판단을 보류한다. 다음은 수집 로그 확인과 비교 가능한 두 집단 실험이며 완료율과 사용자 경험을 관찰한다.')]
        for index,(name,text) in enumerate(texts):
            if index: time.sleep(16)
            result=evaluate_report(provider,session['task'],{'version':1,'text':text+' 실제 근거 '+json.dumps(executions[0]['result']['rows'],ensure_ascii=False)},[],executions,[])
            grade=next(c['grade'] for c in result['criteria'] if c['id']=='decision_limits')
            results.append({'name':name,'passed':not result['held'] and grade>=3,'details':result})
        Path(args.output).write_text(json.dumps({'model':provider.model,'cases':results,'human_review':'pending','live_calls':live_calls[0]},ensure_ascii=False,indent=2),encoding='utf-8')
        for result in results: print(json.dumps({'case':result['name'],'passed':result['passed']}),flush=True)
        return
    service=DiscordTrainingService(store,DiscordQueryEngine(provider,SqlRunner(settings),settings),provider,settings)
    final=service.handle(session['owner_user_id'],session['session_id'],uuid.uuid4().hex,'submit')
    restored=service.resume(session['owner_user_id'],'test-guild',session['session_id'])['session']
    results.append({'name':'live_submission_after_parser_fix','passed':live_calls[0]>0 and restored['state']=='completed' and not restored['evaluations'][-1]['result']['held'],
        'details':{'evaluation':restored['evaluations'][-1],'telemetry':restored['telemetry'],'executions_preserved':restored['executions']==executions}})
    output={'model':provider.model,'cases':results,'human_review':'pending','live_calls':live_calls[0]}
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    for result in results: print(json.dumps({'case':result['name'],'passed':result['passed']}),flush=True)


if __name__=='__main__': main()
