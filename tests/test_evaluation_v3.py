import copy
import json
import pytest
from da_agent.evaluation import freeze_evaluation
from da_agent.evaluation_v3 import grade, normalize, public_rubric


def fixture():
    public = dict(task_kind='calculation', evaluation_version='request-review-v3', weights={'problem_definition':25,'analysis_approach':25,'sql_accuracy':20,'interpretation':20,'next_actions':10}, completion_conditions=['고유 유저 수를 계산하고 근거와 한계를 설명'])
    frozen = freeze_evaluation(public, {'weights':public['weights'], 'expected':{'n':42}})
    report = dict(attempt_id='a', content={'problem_definition':'기간·대상·단위·지표 정의', 'hypothesis':'검증 방법과 순서', 'limitations':'관측 한계와 대안', 'next_actions':'우선 확인 후 종료'}, claims=[dict(claim_id='c',text='확인된 결과 해석',evidence_refs=[dict(saved_execution_id='e')])])
    evidence = [dict(saved_execution_id='e',attempt_id='a',sql='SELECT 42 AS n',result=dict(status='success',result_complete=True,columns=[dict(name='n')],rows=[[42]]))]
    rows=[]
    for criterion in public['evaluation_rubric']['criteria']:
        path={'problem_definition':'report.content.problem_definition','analysis_approach':'report.content.hypothesis','sql_accuracy':'execution:e:sql','interpretation':'claim:c','next_actions':'report.content.next_actions'}[criterion['key']]
        rows.append(dict(key=criterion['key'], conditions=[dict(id=c['id'],state='met',reason='제출 근거 확인',sources=[] if c['kind']=='error' else [dict(path=path,quote='unused')]) for c in criterion['conditions']]))
        if criterion['key']=='sql_accuracy':
            next(c for c in rows[-1]['conditions'] if c['id']=='advanced')['sources']=[dict(path='execution:e:sql',quote=evidence[0]['sql'])]
    response=dict(status='completed',feedback=dict(criteria=rows,strengths=[],improvements=[],next_steps=[],uncertainty=''))
    for row in rows:
        for c in row['conditions']:
            for ref in c['sources']:ref.pop('quote',None)
    return public,frozen,report,evidence,response


def condition(response,key,cid):
    return next(c for r in response['feedback']['criteria'] if r['key']==key for c in r['conditions'] if c['id']==cid)


@pytest.mark.parametrize('states,expected', [({'content':'missing'},0),({'critical':'error'},1),({'target':'missing'},2),({'advanced':'missing'},3),({},4),({'target':'unverifiable','critical':'error'},None)])
def test_grade_priority_and_hold(states,expected):
    p,*_=fixture()
    cs=[dict(c,state=states.get(c['id'],'met')) for c in p['evaluation_rubric']['criteria'][0]['conditions']]
    assert grade(cs)==expected


def test_server_grades_and_rounds_without_ai_levels():
    p,f,r,e,response=fixture()
    results=[normalize(copy.deepcopy(response),r,e,f) for _ in range(3)]
    assert all(x['feedback']['total_score']==100 for x in results)
    condition(response,'problem_definition','advanced')['state']='missing'
    assert normalize(response,r,e,f)['feedback']['total_score']==93.8
    response['feedback']['criteria'][0]['level']=4
    assert normalize(response,r,e,f)['error']['code']=='review_schema'


def test_sql_error_does_not_reduce_valid_plan_or_next_action():
    p,f,r,e,response=fixture()
    e[0]['result']['rows']=[[43]]
    out=normalize(response,r,e,f)['feedback']
    assert {c['key']:c['level'] for c in out['criteria']}==dict(problem_definition=4,analysis_approach=4,sql_accuracy=1,interpretation=4,next_actions=4)


def test_missing_execution_zero_but_truncated_or_no_contract_held():
    p,f,r,e,response=fixture()
    empty_response=copy.deepcopy(response)
    
    for c in next(row for row in empty_response['feedback']['criteria'] if row['key']=='sql_accuracy')['conditions']:
        c.update(state='missing',sources=[])
    out=normalize(empty_response,r,[],f)['feedback']
    assert next(c for c in out['criteria'] if c['key']=='sql_accuracy')['level']==0
    for change in [{'truncated':True},{'result_complete':False},{'columns':[{'name':'unknown'}]}]:
        saved=copy.deepcopy(e);saved[0]['result'].update(change)
        out=normalize(response,r,saved,f)['feedback']
        assert out['total_score'] is None and out['confirmed_weight']==80
        assert next(c for c in out['criteria'] if c['key']=='sql_accuracy')['score'] is None


def test_final_contradiction_and_unverifiable_are_independent():
    p,f,r,e,response=fixture()
    condition(response,'interpretation','critical')['state']='error'
    condition(response,'interpretation','critical')['sources']=[dict(path='claim:c')]
    out=normalize(response,r,e,f)['feedback']
    assert next(c for c in out['criteria'] if c['key']=='interpretation')['level']==1
    condition(response,'problem_definition','target')['state']='unverifiable'
    out=normalize(response,r,e,f)['feedback']
    assert out['total_score'] is None


@pytest.mark.parametrize('mutation,code', [('duplicate','review_schema'),('quote','review_source'),('path','review_source'),('weight','review_contract'),('identity','review_contract')])
def test_invalid_conditions_sources_and_contract_fail_closed(mutation,code):
    p,f,r,e,response=fixture()
    c=condition(response,'problem_definition','target')
    if mutation=='duplicate':response['feedback']['criteria'][0]['conditions'].append(copy.deepcopy(c))
    if mutation=='quote':c['sources'][0]['quote']='없는 실제 발췌'
    if mutation=='path':c['sources'][0]['path']='claim:fake'
    if mutation=='weight':f['weights']['problem_definition']=99
    if mutation=='identity':e[0]['attempt_id']='other'
    assert normalize(response,r,e,f)['error']['code']==code


def test_design_exclusion_and_legacy_freeze_preserved():
    p,f,r,e,response=fixture()
    p['task_kind']='design';p['weights']={'problem_definition':30,'analysis_approach':30,'interpretation':25,'next_actions':15};p.pop('evaluation_rubric')
    frozen=freeze_evaluation(p,{'weights':p['weights']})
    assert frozen['condition_rubric']['excluded_criteria']==['sql_accuracy']
    assert 'sql_accuracy' not in [c['key'] for c in frozen['condition_rubric']['criteria']]
    p.pop('evaluation_version');p.pop('evaluation_rubric')
    assert freeze_evaluation(p,{'weights':p['weights']})['rules_version']=='evaluation-v2'


def test_provider_failure_and_json_errors_are_distinct():
    p,f,r,e,response=fixture()
    assert normalize(dict(state='error',reason='api_unavailable'),r,e,f)['error']['code']=='api_unavailable'
    assert normalize(dict(status='completed',feedback='{'),r,e,f)['error']['code']=='review_json'


def test_grade_metadata_coincidence_does_not_trip_narrative_guard():
    from da_agent.evaluation import exposure_detected
    p,f,r,e,response=fixture()
    private={'expected':{'private_count':25}}
    out=normalize(response,r,e,f,private)
    assert out['status']=='completed'
    assert exposure_detected(out['feedback'],private,{'report':r,'evidence':e}) is True
    response['feedback']['strengths']=['비공개 기준 수치는 25이다']
    assert normalize(response,r,e,f,private)['error']['code']=='answer_exposure'


def test_sql_four_requires_actual_execution_reference():
    p,f,r,e,response=fixture()
    condition(response,'sql_accuracy','advanced')['sources']=[dict(path='report.content.problem_definition')]
    assert normalize(response,r,e,f)['error']['issue']=='out_of_scope_source'


def test_excerpts_are_server_copied_without_model_rewriting():
    p,f,r,e,response=fixture()
    out=normalize(response,r,e,f)['feedback']
    c=next(c for c in out['criteria'] if c['key']=='problem_definition')['conditions'][0]
    assert c['sources'][0]['quote']==r['content']['problem_definition']
    assert c['sources'][0]['excerpt_truncated'] is False


def test_scoped_inputs_exclude_sql_from_plan_and_reject_cross_criterion_refs():
    from da_agent.evaluation_v3 import review_envelope, review_messages
    p,f,r,e,response=fixture()
    envelope=review_envelope(p,r,e,f)
    payload=json.loads(review_messages(envelope)[1]['content'])
    assert 'report' not in payload and 'sources' not in payload
    assert payload['criterion_inputs']['analysis_approach']=={'report.content.hypothesis':r['content']['hypothesis']}
    condition(response,'analysis_approach','critical').update(state='error',sources=[dict(path='execution:e:sql')])
    assert normalize(response,r,e,f)['error']['issue']=='out_of_scope_source'


def test_empty_authored_report_cannot_become_core_error():
    p,f,r,e,response=fixture()
    r['content']={k:'' for k in r['content']}
    r['claims'][0]['text']=''
    for row in response['feedback']['criteria']:
        if row['key']!='sql_accuracy':
            for c in row['conditions']:
                c.update(state='missing' if c['id']!='critical' else 'met',sources=[])
    for c in response['feedback']['criteria'][0]['conditions']:
        c.update(state='error' if c['id']=='critical' else 'missing',sources=[])
    out=normalize(response,r,e,f)['feedback']
    definition=out['criteria'][0]
    assert definition['level']==0
    assert next(c for c in definition['conditions'] if c['id']=='critical')['state']=='met'


def test_valid_plan_in_another_section_is_still_eligible():
    p,f,r,e,response=fixture()
    r['content']['report_text']=r['content'].pop('hypothesis')
    for c in next(row for row in response['feedback']['criteria'] if row['key']=='analysis_approach')['conditions']:
        if c['sources']: c['sources']=[dict(path='report.content.report_text')]
    out=normalize(response,r,e,f)
    assert out['status']=='completed'
    assert next(c for c in out['feedback']['criteria'] if c['key']=='analysis_approach')['level']==4


def test_all_definition_elements_missing_means_no_definition_not_partial_credit():
    p,f,r,e,response=fixture()
    row=response['feedback']['criteria'][0]
    ids={c['id'] for c in f['condition_rubric']['criteria'][0]['conditions'] if c['kind']=='required'}
    for c in row['conditions']:
        if c['id'] in ids: c.update(state='missing',sources=[])
    out=normalize(response,r,e,f)['feedback']
    assert out['criteria'][0]['level']==0
    # One genuinely stated definition element still permits partial credit.
    condition(response,'problem_definition','target').update(state='met',sources=[dict(path='report.content.problem_definition')])
    assert normalize(response,r,e,f)['feedback']['criteria'][0]['level']==2


def test_quality_flags_critical_drift_even_when_total_is_held():
    from da_agent.quality_v2 import summarize
    sample={'id':'core_error','results':[]}
    for state in ['met','error','met']:
        sample['results'].append({'repetition':len(sample['results'])+1,'status':'completed','automatic_verdict':'pass','feedback':{'total_score':None,'criteria':[{'key':'analysis_approach','conditions':[{'kind':'error','state':state}]}]}})
    result=summarize({'run_id':'test','status':'completed','samples':[sample]})
    assert result['verdict']=='fail'
    assert result['samples'][0]['score_range'] is None
    assert result['samples'][0]['critical_state_variation']=={'analysis_approach':['error','met']}


def test_complete_partial_calculation_is_two_and_wrong_subset_is_one():
    p,f,r,e,response=fixture()
    p.pop('evaluation_rubric')
    f=freeze_evaluation(p,{'weights':p['weights'],'expected':{'n':42,'missing_count':8}})
    out=normalize(response,r,e,f)['feedback']
    assert next(c for c in out['criteria'] if c['key']=='sql_accuracy')['level']==2
    assert out['total_score'] is not None
    e[0]['result']['rows']=[[43]]
    out=normalize(response,r,e,f)['feedback']
    assert next(c for c in out['criteria'] if c['key']=='sql_accuracy')['level']==1
