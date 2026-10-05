"""Reject the original failure and verify real analytical calculations and boundaries."""
import copy
import json
import uuid
import pytest
from pydantic import ValidationError
from da_agent.adaptive_tasks import Recipe, Metric, generate_rows, comparison_references, preflight
from da_agent.analytical_metrics import reference
from da_agent.task_quality import check_quality, public_description
from da_agent.task_contracts import RequestV2
from da_agent.errors import DomainError
from da_agent.evaluation import verify_for_contract
from test_adaptive_tasks import bot_recipe
from test_task_generation_v2 import v2_db_client

def test_old_simple_task_is_readable_but_cannot_be_published_as_new_request():
    value=bot_recipe(); value.pop('business_case');value['metrics']=value['metrics'][:3]
    recipe=Recipe.model_validate(value)
    assert recipe.business_case is None
    with pytest.raises(DomainError,match='업무|고정'):
        preflight(recipe,7,RequestV2(contract_version='request-v2',request_id='quality',message='실제 게임 업계 실무에서 발생하는 문제를 분석하고 싶어'))

@pytest.mark.parametrize('change',['missing_question_evidence','advanced_counts','advanced_missing_judgments','no_comparison'])
def test_quality_rejects_missing_support_and_cosmetic_difficulty(change):
    value=bot_recipe()
    if change=='missing_question_evidence':value['business_case']['requirements'][0]['evidence'][0]['columns']=['nonexistent']
    if change.startswith('advanced'):
        value['difficulty']='advanced'
        if change=='advanced_counts':
            for competency in ('alternatives','confounding'):
                req=copy.deepcopy(value['business_case']['requirements'][0]);req['competency']=competency;value['business_case']['requirements'].append(req)
            for metric in value['metrics']:metric['operation']='count';metric.pop('column',None)
    if change=='no_comparison':value['metrics'][-1]['group_by']=[]
    with pytest.raises(ValueError):check_quality(Recipe.model_validate(value))

def test_joined_rate_denominator_groups_and_alternative_evidence():
    value=bot_recipe()
    metric={'name':'regular_share','table':'activity_daily','operation':'ratio','purpose':'analysis',
        'joins':[{'table':'accounts','source_column':'account_id'}], 'group_by':['accounts.platform'],
        'conditions':[{'column':'interval_cv','operator':'lt','value':0.05}],
        'denominator_conditions':[{'column':'actions','operator':'gte','value':1000}]}
    value['metrics'].append(metric);recipe=Recipe.model_validate(value)
    tables={t.name:t for t in recipe.tables}
    rows={'accounts':[{'account_id':1,'platform':'pc'},{'account_id':2,'platform':'mobile'}],
          'activity_daily':[{'activity_id':1,'account_id':1,'actions':1500,'interval_cv':0.02},
                            {'activity_id':2,'account_id':1,'actions':1500,'interval_cv':0.9},
                            {'activity_id':3,'account_id':2,'actions':1500,'interval_cv':0.8},
                            {'activity_id':4,'account_id':2,'actions':10,'interval_cv':0.01}]}
    expected,query=reference(recipe.metrics[-1],tables,rows)
    assert expected=={'columns':['accounts__platform','regular_share'],'rows':[['pc',0.5],['mobile',0.0]]}
    assert 'JOIN "accounts"' in query and 'NULLIF(COUNT(*),0)' in query and 'GROUP BY' in query
    frozen={'expected':{'total':4},'verification_contracts':{'rate':{'comparison_expected':expected}}}
    proof={'saved_execution_id':'rate','result':{'status':'success','result_complete':True,'columns':[{'name':c} for c in expected['columns']], 'rows':[['mobile','0.0'],['pc','0.5']]}}
    assert verify_for_contract(proof,frozen)['status']=='verified'
    proof['result']['rows'][1][1]='0.25'
    assert verify_for_contract(proof,frozen)['status']=='mismatch'
    proof['result']['truncated']=True
    assert verify_for_contract(proof,frozen)['status']=='unverified'

def test_join_fanout_and_sql_identifier_injection_rejected():
    for join in ({'table':'activity_daily','source_column':'account_id'}, {'table':'accounts;drop','source_column':'account_id'}):
        value=bot_recipe();value['metrics'][-1]['joins']=[join]
        with pytest.raises(ValidationError):Recipe.model_validate(value)

def test_empty_denominator_and_single_group_do_not_make_false_comparison():
    recipe=Recipe.model_validate(bot_recipe());tables={t.name:t for t in recipe.tables}
    metric=Metric(name='share',table='activity_daily',operation='ratio',conditions=[{'column':'actions','operator':'gt','value':0}])
    result,_=reference(metric,tables,{'accounts':[],'activity_daily':[]})
    assert result['rows']==[[None]]
    with pytest.raises(ValueError,match='two'):
        reference(recipe.metrics[-1],tables,{'accounts':[],'activity_daily':[]})

def test_agent_context_is_public_but_private_groups_and_reference_numbers_are_not():
    recipe=Recipe.model_validate(bot_recipe())
    description=public_description(recipe)
    assert '에이전트가 설정한 연습 조건' in description and '가상 업무' in description
    assert 'regular_activity_rows' not in description and 'regular_share' not in description
    assert comparison_references(recipe,generate_rows(recipe,10))['platform_actions']['comparison_expected']['rows']

def test_unmeasured_retention_claim_and_unused_profile_table_are_rejected():
    value=bot_recipe();value['goal']='신규 보스 실패로 인한 이용자 이탈 원인 분석'
    with pytest.raises(ValueError,match='retention/churn'):
        check_quality(Recipe.model_validate(value))
    value=bot_recipe();value['metrics'][-1]['joins']=[];value['metrics'][-1]['group_by']=['actions']
    with pytest.raises(ValueError,match='FK-linked'):
        check_quality(Recipe.model_validate(value))

def test_db_joined_comparison_rate_saved_and_matched_without_answer_sql(v2_db_client):
    client,provider=v2_db_client
    value=bot_recipe()
    value['metrics'].append({'name':'regular_share','table':'activity_daily','operation':'ratio','purpose':'analysis',
        'joins':[{'table':'accounts','source_column':'account_id'}],'group_by':['accounts.platform'],
        'conditions':[{'column':'interval_cv','operator':'lt','value':0.05}],
        'denominator_conditions':[{'column':'actions','operator':'gte','value':1000}]})
    provider.review=lambda messages:{'state':'completed','text':json.dumps(value) if 'schema' in json.loads(messages[1]['content']) else '{"aligned":true,"issues":[],"quality_dimensions":{"business_context":"pass","evidence_sufficiency":"pass","difficulty_fit":"pass","evaluation_alignment":"pass"}}'}
    rid=str(uuid.uuid4())
    client.post('/api/training/requests',json={'contract_version':'request-v2','request_id':rid,'message':'비정상 이용자 분석','intentional_repeat':True})
    request=client.get('/api/training/requests/'+rid).json()
    assert request['status']=='ready',request
    aid=request['attempt_id'];attempt=client.get('/api/attempts/'+aid).json()
    package=client.app.state.training.catalog.load(attempt['package_id'],'v1')
    private=package.reference('problem-001')
    for check in private['analytical_checks'].values():
        result=client.post('/api/attempts/'+aid+'/execute',json={'sql':check['sql']}).json()
        assert result['status']=='success',result
        saved=client.post('/api/attempts/'+aid+'/executions/save',json={'request_id':str(uuid.uuid4()),'execution_id':result['execution_id']}).json()
        assert verify_for_contract(saved,private['frozen_evaluation'])['status']=='verified'
    assert 'expected' not in attempt['problem'] and 'analytical_checks' not in attempt['problem']

def test_provider_schema_keeps_fields_named_title_description_and_nested_refs(tmp_path):
    import httpx
    from test_api_provider import provider,completed
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json=completed('{}'))
    value=provider(tmp_path,handler)
    value.review([{'role':'user','content':json.dumps({'request':{},'schema':Recipe.model_json_schema()})}])
    schema=sent[0]['generationConfig']['responseJsonSchema']
    assert {'title','description','business_case'}.issubset(schema['properties'])
    assert 'title' in schema['required'] and '$defs' in schema
    assert 'maxLength' not in schema['properties']['title']

def test_time_dependencies_reorder_without_changing_data_and_missing_fields_fail():
    from test_adaptive_dependencies import event_recipe
    value=event_recipe();original=Recipe.model_validate(value)
    value['tables'][1]['columns'][1:]=list(reversed(value['tables'][1]['columns'][1:]))
    reordered=Recipe.model_validate(value)
    # Reordering fixes forward references; generated time arithmetic remains valid.
    rows=generate_rows(reordered,40)
    from da_agent.adaptive_tasks import timestamp
    for row in rows['events']:
        assert (timestamp(row['ended_at'])-timestamp(row['started_at'])).total_seconds()==row['duration_sec']
    value=event_recipe()
    next(c for c in value['tables'][1]['columns'] if c['name']=='started_at')['generator'].pop('start')
    with pytest.raises(ValidationError,match='non-null'):
        Recipe.model_validate(value)

def test_quality_review_cannot_pass_when_any_dimension_fails():
    from da_agent.adaptive_tasks import validate_alignment
    dimensions={k:'pass' for k in ('business_context','evidence_sufficiency','difficulty_fit','evaluation_alignment')}
    dimensions['difficulty_fit']='fail'
    with pytest.raises(DomainError) as caught:
        validate_alignment({'state':'completed','text':json.dumps({'aligned':True,'issues':[],'quality_dimensions':dimensions})},quality_required=True)
    assert 'difficulty_fit: fail' in caught.value.validation_issues
    with pytest.raises(DomainError):
        validate_alignment({'state':'completed','text':'{"aligned":true,"issues":[]}'},quality_required=True)

def test_generation_schema_preserves_requested_difficulty_and_required_inputs():
    from da_agent.adaptive_tasks import planning_messages
    from da_agent.task_contracts import RequestV2
    request=RequestV2(contract_version='request-v2',request_id='schema-test',
        message='실제 게임 업계 실무에서 발생하는 문제를 분석하고 싶어',difficulty='advanced')
    schema=json.loads(planning_messages(request,[])[1]['content'])['schema']
    assert schema['properties']['difficulty']['enum']==['advanced']
    assert {'groups','unique_keys'}.issubset(schema['$defs']['Table']['required'])
    variants=schema['$defs']['Generator']['anyOf']
    offset=next(v for v in variants if v['properties']['kind']['enum']==['timestamp_offset'])
    assert {'source_column','interval_column'}.issubset(offset['required'])
    assert 'null' not in str(offset['properties']['interval_column'])

def test_measurement_can_combine_sample_count_and_analytical_comparison():
    value=bot_recipe()
    comparison=next(r for r in value['business_case']['requirements'] if r['competency']=='comparison')
    comparison['metric_names'].append(value['metrics'][0]['name'])
    assert check_quality(Recipe.model_validate(value))['structural_checks']=='pass'

def test_investigation_checks_observed_subset_without_requiring_model_guessed_bounds():
    value=bot_recipe()
    for metric in value['metrics']:metric.pop('minimum',None)
    value['metrics'][-1]['conditions']=[{'column':'actions','operator':'gt','value':0}]
    recipe=Recipe.model_validate(value)
    assert preflight(recipe,7)
    for metric in value['metrics']:
        if metric.get('conditions'):metric['conditions']=[{'column':'actions','operator':'gt','value':100000000}]
    with pytest.raises(DomainError,match='고정'):
        preflight(Recipe.model_validate(value),7)
