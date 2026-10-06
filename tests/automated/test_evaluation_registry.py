from copy import deepcopy
from da_agent.discord_education import representative_task
from da_agent.evaluation_quality import contract, fingerprint
from da_agent.evaluation_registry import QualityRegistry, code_hashes, task_key, with_profile_policy
from da_agent.discord_verification import verify_report, apply_verification
from test_discord_verification import evidence


def bundle():
    task=representative_task()
    fp=contract(task)['fingerprint']
    cases=[]
    for ident in ('correct','numeric_error','valid_alternative','uncertainty','missing_evidence','system_failure'):
        grade=1 if ident=='numeric_error' else 2 if ident=='missing_evidence' else 3
        grades=[dict(id=c['id'],grade=grade if c['id']=='evidence_interpretation' else 3,
            reason='고정 표본 근거',improvement='다음 확인',evidence_refs=['report:1'],deductions=[]) for c in task['rubric']['criteria']]
        total=round(sum(c['weight']*g['grade']/4 for c,g in zip(task['rubric']['criteria'],grades)),2)
        report=dict(version=1,text='구성 변화만으로 전체 하락을 완전히 설명한다.' if ident=='numeric_error' else '1주차80%. 2주차70%.',evidence_refs=['q1','q2'])
        executions=evidence()
        proof=verify_report(task,report,executions)
        if ident=='numeric_error': apply_verification(grades,proof,1)
        if ident=='missing_evidence':
            grades[3]['deductions']=[dict(issue_id='missing',kind='missing_required',check_id='evidence_interpretation:required',claim='',evidence_refs=['report:1'],reason='필수 조건 누락')]
        result=dict(held=ident=='system_failure',total=None if ident=='system_failure' else total,
            criteria=grades,
            quality_validation=dict(status='passed',contract_fingerprint=fp),
            arithmetic_verification=proof)
        cases.append(dict(id=ident,report=report,executions=executions,expected_grades={'evidence_interpretation':[grade,grade]},results=[deepcopy(result) for _ in range(3)]))
    fixtures=dict(version='scripted-gate-test',task=task,cases=[{k:v for k,v in c.items() if k!='results'} for c in cases])
    run=dict(status='completed',fixture_fingerprint=fingerprint(fixtures),model='scripted',code_hashes=code_hashes(),cases=cases,summary=dict(repeats=3))
    return fixtures,run


def test_registry_holds_missing_and_changed_models_and_difficulty(tmp_path):
    fixtures,run=bundle(); registry=QualityRegistry(tmp_path)
    assert registry.check(fixtures['task'],'scripted')['status']=='held'
    assert registry.register(fixtures,run,model='scripted')['status']=='eligible'
    assert registry.check(fixtures['task'],'scripted')['status']=='eligible'
    assert registry.check(fixtures['task'],'other')['status']=='held'
    changed=deepcopy(fixtures['task']); changed['difficulty']='advanced'
    assert registry.check(changed,'scripted')['status']=='held'


def test_failed_run_archived_without_overwriting_valid_profile(tmp_path):
    fixtures,run=bundle(); registry=QualityRegistry(tmp_path)
    registry.register(fixtures,run,model='scripted')
    original=(tmp_path/task_key(fixtures['task'])/'profile.json').read_bytes()
    run['cases'][0]['results'][0]['held']=True
    assert registry.register(fixtures,run,model='scripted')['status']=='held'
    assert (tmp_path/task_key(fixtures['task'])/'profile.json').read_bytes()==original
    assert len(list((tmp_path/task_key(fixtures['task'])).glob('*.json')))==3


def test_profile_score_hold_preserves_feedback_and_original_record():
    result=dict(held=False,total=100,criteria=[dict(id='evidence_interpretation',grade=4,reason='피드백')])
    before=deepcopy(result)
    held=with_profile_policy(result,{'status':'held','reasons':['pending']})
    assert held['held'] and held['total'] is None and held['candidate_total']==100
    assert held['criteria']==result['criteria'] and result==before


def test_changed_code_or_captured_expectation_invalidates_profile(tmp_path):
    fixtures,run=bundle(); registry=QualityRegistry(tmp_path)
    run['code_hashes']['evaluation_quality.py']='changed'
    assert registry.register(fixtures,run,model='scripted')['status']=='held'
    fixtures,run=bundle(); run['cases'][0]['expected_grades']={'evidence_interpretation':[0,4]}
    assert registry.register(fixtures,run,model='scripted')['status']=='held'
