"""Revalidate preserved raw outputs with current code; no fresh model calls.

This is parser/gate regression evidence, not a new stochastic-model repetition.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from da_agent.discord_education import evaluate_report
from da_agent.evaluation_quality import contract, summarize_suite, quality_gate
from da_agent.evaluation_registry import code_hashes


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('fixtures'); parser.add_argument('run'); parser.add_argument('output')
    args=parser.parse_args()
    fixtures=json.loads(Path(args.fixtures).read_text(encoding='utf-8-sig'))
    run=json.loads(Path(args.run).read_text(encoding='utf-8-sig'))
    output=deepcopy(run)
    cursor=0
    for case in output['cases']:
        old=deepcopy(case['results']); case['results']=[]
        for index,record in enumerate(old):
            if case['id']=='system_failure':
                values=[dict(state='error',reason='scripted_api_failure')]
            else:
                ranges=case.get('call_ranges',[])
                if ranges:
                    bounds=ranges[index]
                    values=run['calls'][bounds['start']:bounds['end']]
                else:
                    count=1+record.get('response_repair',{}).get('attempts',0)
                    values=run['calls'][cursor:cursor+count]; cursor+=count
            responses=iter(values)
            provider=SimpleNamespace(review=lambda _:deepcopy(next(responses)))
            current=evaluate_report(provider,fixtures['task'],case['report'],[],case.get('executions',[]),[])
            case['results'].append(current)
    output['verification_kind']='recorded_response_revalidation_no_new_model_calls'
    output['original_status']=output['status']
    output['status']='recorded_revalidation'
    output['original_code_hashes']=output.get('code_hashes')
    output['code_hashes']=code_hashes()
    output['summary']=summarize_suite(output['cases'],run['summary']['repeats'],contract(fixtures['task'])['fingerprint'])
    output['gate']=quality_gate(fixtures,output,model=run['model'],code_hashes=output['code_hashes'])
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
