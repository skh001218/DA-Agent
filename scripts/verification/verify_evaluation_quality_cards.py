"""Real disposable record DB, scripted judgment, stored-query evidence, no sends."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit,urlunsplit
from uuid import uuid4
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_store import DiscordStore


def main():
    source=json.loads(Path('/tmp/spec033-source.json').read_text(encoding='utf-8'))['session']
    dsn=urlunsplit(urlsplit(os.environ['DISCORD_ADMIN_DSN'])._replace(path='/discord_test'))
    store=DiscordStore(dsn); store.initialize()
    def review(messages):
        context=json.loads(messages[-1]['content'])
        return dict(state='completed',text=json.dumps(dict(criteria=[dict(id=c['id'],grade=4,
            reason='화면 검증용 공급자 판정',improvement='다음 확인',deductions=[],
            evidence_refs=[f"report:{context['report']['version']}"]) for c in context['public_task']['rubric']['criteria']]),ensure_ascii=False))
    provider=SimpleNamespace(model='scripted-screen-fixture',review=review)
    service=DiscordTrainingService(store,None,provider,SimpleNamespace(daily_call_limit=10),
        dataset_factory=lambda *_:dict(schema_name='no_sql_execution_in_screen_check',data_version=source['task']['data_version']))
    owner='spec033-screen-'+uuid4().hex
    doc=service.start(owner,'verification','verification',uuid4().hex,difficulty='advanced', practice='analysis')
    sid=doc['session_id']
    with store.edit(owner,sid) as (record,conn):
        record['executions']=deepcopy(source['executions'])
        record['selected_evidence']=[e['execution_id'] for e in source['executions']]
    def handle(action,text=''):
        return service.handle(owner,sid,uuid4().hex,action,text)
    handle('report',source['reports'][0]['content']['report_text'])
    handle('followup','추가 로그를 확인한다.')
    first=handle('submit')['session']
    original=deepcopy(first['evaluations'][0])
    assert original['result']['total'] is None and original['result']['quality_profile']['status']=='held'
    assert len(handle('submit')['session']['evaluations'])==1
    handle('report',source['reports'][1]['content']['report_text'])
    handle('followup','확인할 지표와 관측 조건을 정한다.')
    final=handle('submit')['session']
    assert final['evaluations'][0]==original
    feedback=final['evaluations'][1]['revision_feedback']
    assert feedback['resolved'] and not feedback['remaining']
    restored=DiscordTrainingService(DiscordStore(dsn),None,provider,SimpleNamespace()).resume(owner,'verification',sid)['session']
    assert restored['evaluations']==final['evaluations']
    out=Path('/tmp/spec033/screen.json')
    out.write_text(json.dumps(dict(session=final,verification_kind='real_record_db_scripted_judgment_stored_query_evidence',
         restored=True,discord_transport='not_sent'),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'score_held':True,'resolved':len(feedback['resolved']),'preserved':True,'restored':True}))


if __name__=='__main__': main()
