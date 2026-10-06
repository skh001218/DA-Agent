"""Bounded fixed-fixture repetitions; expectations never enter model input.

Run with an explicit fixtures JSON. New subjects reuse this runner after their
public criteria, saved evidence, expected grades and metric rules are frozen.
No database writes, Discord sends, background scheduling or automatic approval.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

from da_agent.discord_education import evaluate_report
from da_agent.discord_provider import DiscordGemmaProvider
from da_agent.evaluation_quality import contract, fingerprint, summarize_suite, quality_gate
from da_agent.evaluation_registry import code_hashes, QualityRegistry


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('fixtures')
    parser.add_argument('output')
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--interval',type=float,default=16)
    parser.add_argument('--max-calls',type=int,default=30)
    parser.add_argument('--only',nargs='+',default=[])
    parser.add_argument('--register',help='Write an active profile only if the complete gate passes')
    args=parser.parse_args()
    if not 1 <= args.repeats <= 5 or not 0 <= args.interval <= 60 or not 1 <= args.max_calls <= 60:
        parser.error('bounded repeats1..5, interval0..60, max-calls1..60 required')
    fixtures=json.loads(Path(args.fixtures).read_text(encoding='utf-8-sig'))
    task=fixtures['task']
    cases=deepcopy(fixtures['cases'])
    if len({c['id'] for c in cases}) != len(cases): raise ValueError('duplicate case IDs')
    quality=contract(task)
    for case in cases:
        case['results']=[]
        case['call_ranges']=[]
        if not case.get('expected_grades') and case['id'] != 'system_failure':
            raise ValueError('Freeze expected grades before calling the model')
    provider=DiscordGemmaProvider(key_file=os.getenv('DISCORD_API_KEY_FILE','/run/secrets/gemma_key'),
                                 model=os.getenv('DISCORD_MODEL','gemma-4-26b-a4b-it'))
    original, calls, last, unavailable=provider.review, [], [0.0], [0]
    def review(messages):
        if len(calls)>=args.max_calls: return {'state':'error','reason':'verification_call_budget'}
        time.sleep(max(0,args.interval-(time.monotonic()-last[0])))
        result=original(messages)
        unavailable[0] = unavailable[0]+1 if result.get('reason') in ('api_unavailable','api_rate_limited','api_key_invalid','api_permission_denied') else 0
        last[0]=time.monotonic()
        calls.append({k:deepcopy(result[k]) for k in ('state','reason','text','model','usage','quota_diagnostic','provider_diagnostic') if k in result})
        print(json.dumps({'call':len(calls),'state':result['state'],'reason':result.get('reason')},ensure_ascii=False),flush=True)
        return result
    provider.review=review
    out=Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    hashes=code_hashes()
    data={'fixture_version':fixtures['version'],'fixture_fingerprint':fingerprint(fixtures),
          'model':provider.model,'contract':quality,'cases':cases,'calls':calls,'code_hashes':hashes,
          'source':fixtures.get('source'),'discord_transport':'not_sent','status':'running',
          'purpose':'candidate_score_diagnostics_not_learner_scores'}
    def save():
        data['summary']=summarize_suite(cases,args.repeats,quality['fingerprint'])
        data['gate']=quality_gate(fixtures,data,model=provider.model,code_hashes=hashes)
        temp=out.with_suffix('.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(out)
    save()
    try:
        for case in cases:
            if args.only and case['id'] not in args.only: continue
            for index in range(args.repeats):
                engine=SimpleNamespace(review=lambda _:dict(state='error',reason='scripted_api_failure')) if case['id']=='system_failure' else provider
                start=len(calls)
                result=evaluate_report(engine,task,case['report'],[],case.get('executions',[]),[])
                case['results'].append(result)
                case['call_ranges'].append({'start':start,'end':len(calls)})
                save()
                print(json.dumps({'case':case['id'],'repeat':index+1,'held':result['held'],'total':result['total']},ensure_ascii=False),flush=True)
                if len(calls)>=args.max_calls:
                    data['status']='call_budget_exhausted'; return
                if unavailable[0]>=3:
                    data['status']='provider_unavailable'; return
        data['status']='completed'
    except KeyboardInterrupt:
        data['status']='interrupted'
        raise
    finally:
        save()
        if args.register:
            data['registration']=QualityRegistry(args.register).register(fixtures,data,model=provider.model)
            save()
    print(json.dumps(data['summary'],ensure_ascii=False),flush=True)


if __name__=='__main__': main()
