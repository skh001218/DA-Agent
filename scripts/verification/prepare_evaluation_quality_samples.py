"""Freeze six public reports against already saved live execution evidence."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from da_agent.evaluation_quality import fingerprint


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('output')
    args=parser.parse_args()
    source=json.loads(Path(args.source).read_text(encoding='utf-8-sig'))
    doc=source['session']
    task=doc['task']
    good=deepcopy(doc['reports'][1])
    good.update(version=1,followup_answers=[])
    bad=deepcopy(doc['reports'][0]); bad.update(version=1,followup_answers=[])
    criteria={c['id']:[3,4] for c in task['rubric']['criteria']}
    alternate=deepcopy(good)
    alternate['content']['report_text'] += ' 채널별 고유 사용자 분자·분모를 각각 더해 전체 비율을 확인하는 경로도 같은 정의로 사용할 수 있다.'
    uncertain=deepcopy(good)
    uncertain['content']['report_text'] += ' ads 변화의 실제 원인은 미확인이다. 원인 가설은 보류하고 추가 로그와 비교 설계로 검증하겠다.'
    missing={'version':1,'text':'완료율의 원인은 확인됐다. 분석 대상·기간·분모와 실행 근거는 작성하지 않았다.',
             'evidence_refs':[]}
    cases=[]
    for ident, report, expected in [('correct',good,criteria),
        ('numeric_error',bad,{**criteria,'evidence_interpretation':[0,1]}),
        ('valid_alternative',alternate,criteria),('uncertainty',uncertain,criteria),
        ('missing_evidence',missing,{'evidence_interpretation':[0,2]}),('system_failure',good,{})]:
        cases.append({'id':ident,'report':report,'executions':deepcopy(doc['executions']) if ident!='missing_evidence' else [],
                      'expected_grades':deepcopy(expected)})
    data={'version':'discord-tutorial-quality-v1','task':task,'cases':cases,
          'source':{'kind':'saved_real_queries','artifact':Path(args.source).name,
                    'executions_fingerprint':fingerprint(doc['executions'])},
          'expectation_note':'Freeze before evaluation. Human semantic review pending.'}
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
