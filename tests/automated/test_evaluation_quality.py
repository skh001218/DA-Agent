from copy import deepcopy
import pytest
import json
from types import SimpleNamespace

from da_agent.discord_education import representative_task, evaluate_report
from da_agent.evaluation_quality import contract, validate_deductions, revision_feedback, summarize_suite, quality_gate, fingerprint
from da_agent.discord_verification import verify_report
from test_discord_verification import evidence


def rows():
    return [dict(id=c['id'],grade=4,reason='현재 공개 기준 충족',improvement='추가 확인',
                 evidence_refs=['report:1'],deductions=[]) for c in representative_task()['rubric']['criteria']]


def deduction(cid='hypothesis_review', kind='core_error', quote='관측으로 원인을 확정했다.', ident='cause-1'):
    return dict(issue_id=ident,kind=kind,check_id=cid+':core_error',claim=quote,
                evidence_refs=['report:1'],reason='공개 관측 한계를 넘은 확정')


def check(data, text='관측으로 원인을 확정했다.', verification=None):
    return validate_deductions(data,contract(representative_task()),text,{'report:1':{}},verification or {'errors':[]})


def test_renamed_issue_cannot_hide_same_fact_duplicate():
    data=rows()
    for row in data[2:4]:
        row.update(grade=1,deductions=[deduction(row['id'],ident=row['id'])])
    assert {i['code'] for i in check(data)} == {'duplicate_deduction'}


def test_independent_missing_requirements_not_deduplicated():
    data=rows()
    for row in data[:2]:
        row.update(grade=2,deductions=[dict(issue_id=row['id'],kind='missing_required',
            check_id=row['id']+':required',claim='',evidence_refs=['report:1'],reason='해당 필수 조건 누락')])
    assert not check(data)


def test_bad_quote_wrong_owner_unknown_check_and_ambiguous_cause():
    data=rows()
    row=data[2]; row.update(grade=1,deductions=[deduction(kind='causal_claim',quote='원인을 확정하지 않는다.')])
    assert {'deduction_quote_missing','duplicate_error_owner','causal_claim_ambiguous'} <= {i['code'] for i in check(data)}
    row['deductions'][0]=deduction(quote='존재하지 않는 문장')
    row['deductions'][0]['check_id']='private:invented'
    assert {'deduction_quote_missing','deduction_check_mismatch'} <= {i['code'] for i in check(data)}


def test_low_grade_without_ledger_is_provider_hold_not_zero():
    data=rows(); data[0]['grade']=2
    output=evaluate_report(lambda _:dict(criteria=data),representative_task(),{'version':1,'text':'보고'},[],[],[])
    assert output['held'] and output['total'] is None
    assert output['quality_validation']['issues'][0]['code']=='missing_deduction'


@pytest.mark.parametrize('kind',['format','quality'])
def test_repair_provider_failure_preserves_diagnostics_original_and_budget(kind):
    original=rows(); original[0]['grade']=2
    if kind=='format': original[0]['improvement']=''
    failure=dict(state='error',reason='api_rate_limited',quota_diagnostic={'scope':'test'},
                 provider_diagnostic={'status':429})
    responses=iter([dict(state='completed',text=json.dumps(dict(criteria=original))),failure])
    calls=[]
    def review(messages):
        calls.append(messages); return next(responses)
    result=evaluate_report(SimpleNamespace(review=review),representative_task(),{'version':1,'text':'보고'},[],[],[])
    assert result['held'] and result['total'] is None and len(calls)==2
    repair=result['response_repair']
    assert repair['attempts']==1 and repair['status']=='failed' and repair['kind']==kind
    assert repair['original_criteria']==original
    assert result['provider_failure']['reason']=='api_rate_limited'
    assert repair['provider_failure']['provider_diagnostic']=={'status':429}


def test_one_repair_preserves_unaffected_grades_and_input():
    original=rows(); original[0]['grade']=2
    repaired=rows(); repaired[0]['grade']=3
    outputs=iter([original,repaired]); calls=[]
    def review(messages):
        calls.append(messages)
        return dict(state='completed',text=json.dumps(dict(criteria=next(outputs))))
    before=deepcopy(original)
    output=evaluate_report(SimpleNamespace(review=review),representative_task(),{'version':1,'text':'보고'},[],[],[])
    assert not output['held'] and len(calls)==2 and original==before
    assert output['response_repair']['unaffected_grades_preserved']
    assert output['response_repair']['original_criteria']==before


def test_repair_cannot_change_unaffected_grade_or_loop():
    for mutate in (True,False):
        original=rows(); original[0]['grade']=2
        repaired=deepcopy(original)
        if mutate: repaired[1]['grade']=3
        calls=[]
        def review(messages):
            calls.append(1)
            return dict(state='completed',text=json.dumps(dict(criteria=original if len(calls)==1 else repaired)))
        output=evaluate_report(SimpleNamespace(review=review),representative_task(),{'version':1,'text':'보고'},[],[],[])
        assert output['held'] and output['total'] is None and len(calls)==2


def test_known_arithmetic_disguised_as_hypothesis_is_held():
    text='구성 변화만으로 전체 하락을 완전히 설명한다.'
    data=rows(); data[2].update(grade=2,reason='구성 계산 오류',deductions=[deduction(quote=text)])
    result=evaluate_report(lambda _:dict(criteria=data),representative_task(),
        {'version':1,'text':text,'evidence_refs':['q1','q2']},[],evidence(),[])
    assert result['held']
    assert 'possible_arithmetic_duplicate' in {i['code'] for i in result['quality_validation']['issues']}


def test_revision_deleted_claim_is_unverified_not_resolved():
    task=representative_task()
    def verify(text, executions=None):
        return {'arithmetic_verification':verify_report(task,{'version':1,'text':text,'evidence_refs':['q1','q2']},executions or evidence())}
    old=verify('구성 변화만으로 전체 하락을 완전히 설명한다.')
    corrected=verify('1주차 비율을 고정하고 구성만 바꾸면 전체80%가 된다.')
    feedback=revision_feedback(old,corrected)
    assert len(feedback['resolved'])==1 and not feedback['remaining']
    deleted=revision_feedback(old,verify('1주차80%. 2주차70%.'))
    assert not deleted['resolved'] and deleted['unverified']
    changed=evidence(); changed[0]['data_version']='new'
    assert revision_feedback(old,verify('구성만 바꾸면 전체80%가 된다.',changed))['status']=='not_comparable'


def test_repeat_gate_never_passes_missing_runs_or_large_spread():
    out=summarize_suite([],3,'hash')
    assert out['verdict']=='fail'
    case=dict(id='correct',expected_grades={'evidence_interpretation':[3,4]},results=[
        dict(held=False,total=score,criteria=[dict(id='evidence_interpretation',grade=4)],
             quality_validation=dict(status='passed')) for score in (75,100)])
    out=summarize_suite([case],3,'hash')
    assert '반복 수 미충족' in out['cases'][0]['failures']
    assert any('점수 편차' in s for s in out['cases'][0]['failures'])


def test_certificate_recomputes_all_cases_and_rejects_changes():
    from test_evaluation_registry import bundle
    fixtures,run=bundle(); run.update(model='test',code_hashes={'a':'b'})
    assert quality_gate(fixtures,run,model='test',code_hashes={'a':'b'})['status']=='eligible'
    assert quality_gate(fixtures,run,model='other',code_hashes={'a':'b'})['status']=='held'
    run['cases'][0]['results'].pop()
    assert quality_gate(fixtures,run,model='test',code_hashes={'a':'b'})['status']=='held'
