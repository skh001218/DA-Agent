"""Replay a stored failed design offline; never copy private drafts into the report.

Run against the bot's read-only records connection. No model or Discord writes.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from da_agent.discord_design import planning_messages, repair_messages
from da_agent.discord_generation import request
from da_agent.discord_provider import DiscordGemmaProvider


def replay(container, session_id):
    remote = '''import os,json,psycopg,sys
sid=json.loads(sys.argv[1])
with psycopg.connect(os.environ['DISCORD_RECORDS_DSN'],options='-c default_transaction_read_only=on') as conn:
 d=conn.execute('SELECT document FROM discord_records.sessions WHERE session_id=%s',(sid,)).fetchone()[0]
 j=conn.execute('SELECT private FROM discord_records.generation_jobs WHERE session_id=%s',(sid,)).fetchone()[0]
 print(json.dumps({'document':d,'job':j},ensure_ascii=False))
'''
    result=subprocess.run(['docker','exec','-i',container,'python','-c',remote,json.dumps(session_id)],
                          capture_output=True,encoding='utf-8',check=True)
    stored=json.loads(result.stdout); doc=stored['document']; job=stored['job']
    base=planning_messages(request(doc['generation']['message'],session_id,doc['difficulty']),[])
    captures=[]
    def respond(req):
        captures.append(json.loads(req.content))
        return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{}'}]}}]})
    provider=DiscordGemmaProvider(key_file=ROOT/'.local/nonexistent-offline-key',
        http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    provider._key=lambda:'offline-fixture'
    scenarios=[('initial',base)]
    for index,draft in enumerate(job.get('rejected_designs',[])):
        scenarios.append((f'correction_{index+1}',repair_messages(base,draft['text'],draft['issues'],repeated=index>0)))
    records=[]
    for label,messages in scenarios:
        response=provider.review(messages)
        assert response['state']=='completed',response
        diagnostic=response['input_diagnostic']
        assert diagnostic['estimated_input_tokens']<14000
        body=captures[-1]; envelope=json.loads(body['contents'][0]['parts'][0]['text'])
        records.append({'scenario':label,'estimated_input_tokens':diagnostic['estimated_input_tokens'],
                        'headroom':14000-diagnostic['estimated_input_tokens'],
                        'drafts_in_request':sum(c['role']=='model' for c in envelope.get('rejected_design_history',[]))})
        if label!='initial': assert records[-1]['drafts_in_request']==1
    return {'date':'2026-10-07','session_id':session_id,'external_model_calls':0,
            'discord_writes':0,'private_drafts_written':False,'scenarios':records,
            'limitation':'Offline request construction only; no live model success or deployment claim.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--container',default='da-agent-discord-bot-1')
    parser.add_argument('--session-id',required=True)
    parser.add_argument('--output',default='tests/artifacts/generation-repair-2026-10-07.json')
    args=parser.parse_args()
    report=replay(args.container,args.session_id)
    path=ROOT/args.output
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
