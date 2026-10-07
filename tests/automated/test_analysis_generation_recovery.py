"""Model-independent regressions observed during analysis generation."""
from copy import deepcopy
import json
import pytest
from pydantic import ValidationError
from da_agent.adaptive_tasks import Recipe, generate_rows
from da_agent.discord_design import structure_examples, structural_issues, planning_messages, repair_messages, relational_context, requirement_issues, quality_issues
from da_agent.discord_generation import request
from test_adaptive_tasks import bot_recipe

def example_recipe():
    raw = bot_recipe()
    raw['task_kind'] = 'calculation'
    examples = structure_examples()
    raw['tables'] = examples['tables'] + [examples['optional_derived_table_shape']]
    raw['metrics'] = [{'name':'rows','table':'example_summary','operation':'count','minimum':1}]
    return raw


def test_initial_examples_do_not_suggest_optional_derived_or_frequency_complexity():
    messages=planning_messages(request('실무에서 발생하는 문제를 분석하고 싶어','sid','advanced'),[])
    payload=json.loads(messages[-1]['content'])
    assert {'tables','metric_shapes'}<=set(payload['structure_examples'])
    assert not any(k.startswith('optional_') for k in payload['structure_examples'])
    assert '파생 표를 추가' in messages[0]['content']
    assert 'confounding' in messages[0]['content'] and 'confounding' in payload['required_competencies']
    assert payload['request']['message']=='실무에서 발생하는 문제를 분석하고 싶어'


def test_diagnostic_subset_rule_is_in_independent_feedback_without_changing_task_kind():
    raw=example_recipe();raw['task_kind']='investigation'
    before=deepcopy(raw)
    issues=structural_issues(json.dumps(raw))
    detail=next(i for i in issues if i['code']=='diagnostic_subset')
    assert detail['location']==['task_kind']
    assert '사용자가 원인 진단을 명시했다면 유형을 바꾸지 말고' in detail['message']
    with pytest.raises(ValidationError,match='nonempty diagnostic subset'):
        Recipe.model_validate(raw)
    raw['metrics'][0].update(purpose='analysis',conditions=[{'column':'mean_duration','operator':'gt','value':10}])
    assert not any(i['code']=='diagnostic_subset' for i in structural_issues(json.dumps(raw)))
    assert before['task_kind']=='investigation'


def test_filtered_single_category_distinct_means_presence_not_frequency():
    raw=example_recipe()
    events=raw['tables'][1]
    events['columns'].append({'name':'item_id','description':'사용 아이템',
        'generator':{'kind':'category','values':['buff_exp_01','other']}})
    summary=raw['tables'][2]
    for operation,name in [('distinct','used_buff'),('count','buff_count')]:
        summary['columns'].append({'name':name,'description':name,'generator':{
            'kind':'aggregate','source_column':'item_id','operation':operation,'value_type':'integer',
            'conditions':[{'column':'item_id','operator':'eq','value':'buff_exp_01'}]}})
    rows=generate_rows(Recipe.model_validate(raw),23)
    for row in rows['example_summary']:
        count=sum(e['account_id']==row['account_id'] and e['item_id']=='buff_exp_01'
                  for e in rows['example_events'])
        assert row['buff_count']==count and row['used_buff']==int(count>0)
    assert {r['used_buff'] for r in rows['example_summary']}=={0,1}
    assert max(r['buff_count'] for r in rows['example_summary'])>1


def test_total_row_budget_feedback_reports_each_declared_group_without_scaling():
    raw=example_recipe()
    raw['tables'][0]['groups'][0]['count']=150
    raw['tables'][1]['groups'][0]['count']=2000
    before=deepcopy(raw)
    issues=structural_issues(json.dumps(raw))
    detail=next(i for i in issues if i['code']=='total_row_budget')
    assert '2150' in detail['message'] and 'example_accounts' in detail['message']
    assert "['tables', 1, 'groups', 0, 'count']" in detail['message']
    with pytest.raises(ValidationError,match='declared rows=2150'):
        Recipe.model_validate(raw)
    assert raw==before


def test_structure_examples_generate_consistent_real_rows():
    recipe = Recipe.model_validate(example_recipe())
    rows = generate_rows(recipe,23)
    assert len(rows['example_accounts']) == 20
    assert len(rows['example_events']) == 100
    summary = rows['example_summary']
    assert len({r['id'] for r in summary}) == len(summary)
    assert len({r['account_id'] for r in summary}) == len(summary)
    for record in summary:
        observed = [r['duration'] for r in rows['example_events'] if r['account_id']==record['account_id']]
        assert record['mean_duration'] == pytest.approx(sum(observed)/len(observed))


def test_frequency_shape_changes_counts_through_source_foreign_key_pools():
    raw=example_recipe()
    shape=structure_examples()['optional_frequency_group_shape']
    raw['tables'][0]['groups']=shape['account_groups']
    raw['tables'][1]['groups']=shape['event_groups']
    raw['tables'][2]['columns'].append({'name':'event_count','description':'원본 이벤트 수',
        'generator':{'kind':'aggregate','operation':'count','source_column':'id','value_type':'integer'}})
    recipe=Recipe.model_validate(raw)
    rows=generate_rows(recipe,23)
    assert len(rows['example_events'])==400
    assert len(rows['example_accounts'])==22
    assert sum(r['event_count']>50 for r in rows['example_summary'])==2
    assert any(r['event_count']<=50 for r in rows['example_summary'])
    assert recipe.tables[2].groups==[]
    for summary in rows['example_summary']:
        assert summary['event_count']==sum(r['account_id']==summary['account_id'] for r in rows['example_events'])


def test_fk_cannot_be_replaced_with_category_ids_in_group_override():
    raw=example_recipe()
    raw['tables'][1]['groups'][0]['overrides']['account_id']={'kind':'category','values':['1','2']}
    before=deepcopy(raw)
    with pytest.raises(ValidationError,match='override changes type'):
        Recipe.model_validate(raw)
    issues=structural_issues(json.dumps(raw))
    issue=next(i for i in issues if i['code']=='override_generator_kind')
    assert issue['location']==['tables',1,'groups',0,'overrides','account_id','kind']
    assert 'foreign_key' in issue['message'] and 'category' in issue['message']
    assert '앞선 프로필에 작은 계정 그룹' in issue['message']
    assert raw==before


def test_thousand_row_group_fits_the_unchanged_total_dataset_budget():
    raw=example_recipe()
    raw['tables'][1]['groups'][0]['count']=1000
    recipe=Recipe.model_validate(raw)
    rows=generate_rows(recipe,23)
    assert len(rows['example_events'])==1000
    assert sum(map(len,rows.values()))<=2000
    for record in rows['example_summary']:
        observed=[r['duration'] for r in rows['example_events'] if r['account_id']==record['account_id']]
        assert record['mean_duration']==pytest.approx(sum(observed)/len(observed))
    raw['tables'][0]['groups'][0]['count']=1200
    with pytest.raises(ValidationError,match='declared rows=2200'):
        Recipe.model_validate(raw)


def test_total_budget_counts_derived_rows_before_a_later_source_table():
    raw=example_recipe()
    raw['tables'][0]['groups'][0]['count']=500
    raw['tables'][1]['groups'][0]['count']=500
    raw['tables'][1]['unique_keys']=[['account_id']]
    raw['tables'].append({'name':'extra_observations','grain':'독립 관측별 한 행',
        'columns':[{'name':'id','description':'관측 식별자','generator':{'kind':'id'}},
                   {'name':'value','description':'관측값','generator':{'kind':'integer','minimum':0,'maximum':1}}],
        'groups':[{'name':'sample','count':1000,'overrides':{}}]})
    recipe=Recipe.model_validate(raw)  # Exactly 2000 source rows, plus 500 derived.
    with pytest.raises(ValueError,match='including derived tables') as caught:
        generate_rows(recipe,23)
    assert 'actual plus pending rows=2500' in str(caught.value)
    assert "'example_summary': 500" in str(caught.value) and "'extra_observations': 1000" in str(caught.value)


def test_derived_overflow_reports_actual_table_sizes_for_model_repair():
    raw=example_recipe()
    raw['tables'][0]['groups'][0]['count']=1000
    raw['tables'][1]['groups'][0]['count']=1000
    raw['tables'][1]['unique_keys']=[['account_id']]
    recipe=Recipe.model_validate(raw)
    with pytest.raises(ValueError,match='derived data exceeds total row limit=2000') as caught:
        generate_rows(recipe,23)
    assert 'actual rows=3000' in str(caught.value)
    assert "'example_summary': 1000" in str(caught.value)


def test_derived_primary_id_is_not_a_substitute_for_the_missing_original_group_key():
    raw=example_recipe()
    summary=raw['tables'][2]
    summary['columns'].pop(1)
    summary['columns'][0]['name']='account_id'
    before=deepcopy(raw)
    with pytest.raises(ValidationError,match='exposed group_key source_columns='):
        Recipe.model_validate(raw)
    issue=next(i for i in structural_issues(json.dumps(raw)) if i['code']=='group_key_coverage')
    assert issue['location']==['tables',2,'columns'] and 'account_id' in issue['message']
    assert '독립 식별자' in issue['message'] and 'kind=group_key' in issue['message']
    assert raw==before


def test_missing_base_range_and_reversed_override_have_exact_repair_locations():
    raw=example_recipe()
    del raw['tables'][1]['columns'][2]['generator']['minimum']
    raw['tables'][1]['groups'][0]['overrides']['duration']={'kind':'number','minimum':100,'maximum':50}
    before=deepcopy(raw)
    with pytest.raises(ValidationError,match='numeric range'):
        Recipe.model_validate(raw)
    issues=structural_issues(json.dumps(raw))
    locations=[i['location'] for i in issues if i['code']=='numeric_range']
    assert ['tables',1,'columns',2,'generator'] in locations
    assert ['tables',1,'groups',0,'overrides','duration'] in locations
    assert raw==before


@pytest.mark.parametrize('generator,valid',[
    ({'kind':'integer','minimum':1,'maximum':1},True),
    ({'kind':'number','minimum':5,'maximum':5},True),
    ({'kind':'integer','minimum':0.5,'maximum':1.5},False),
    ({'kind':'number','minimum':0,'maximum':1e10},False),
])
def test_numeric_range_feedback_matches_generation_constraints(generator,valid):
    raw=example_recipe()
    raw['tables'][1]['columns'][2]['generator']=generator
    problems=[i for i in structural_issues(json.dumps(raw)) if i['code']=='numeric_range']
    assert bool(problems) is not valid
    if valid:
        rows=generate_rows(Recipe.model_validate(raw),23)
        assert all(r['duration']==generator['minimum'] for r in rows['example_events'])
    else:
        with pytest.raises(ValidationError):
            Recipe.model_validate(raw)


def test_empty_diagnostic_reports_real_ranges_without_changing_threshold_or_rows():
    from da_agent.adaptive_tasks import preflight
    from da_agent.errors import DomainError
    raw=bot_recipe()
    raw['metrics'][1]['conditions'][0]['value']=100000
    before=deepcopy(raw)
    recipe=Recipe.model_validate(raw)
    rows=generate_rows(recipe,2)
    with pytest.raises(DomainError) as caught:
        preflight(recipe,2)
    issue=caught.value.validation_issues[0]
    start=issue['message'].index('[')
    diagnostic=json.JSONDecoder().raw_decode(issue['message'][start:])[0][0]
    actual=[r['actions'] for r in rows['activity_daily']]
    assert diagnostic['observed_table_ranges']['activity_daily.actions']=={
        'minimum':min(actual),'maximum':max(actual)}
    assert diagnostic['conditions'][0]['value']==100000
    feedback=json.loads(repair_messages(planning_messages(request('실무 분석','sid','intermediate'),[]),
        json.dumps(raw),caught.value.validation_issues)[-1]['content'])
    assert '로그 전체 행 수와 계정당 반복 횟수는 다릅니다' in feedback['instruction']
    assert '탐지 기준을 임의로 낮추거나' in feedback['instruction']
    assert raw==before and rows==generate_rows(recipe,2)


def test_missing_judgment_does_not_hide_independent_empty_fixed_data_error():
    from da_agent.adaptive_tasks import preflight
    from da_agent.errors import DomainError
    raw=bot_recipe()
    raw['business_case']['requirements']=[r for r in raw['business_case']['requirements']
                                        if r['competency']!='uncertainty']
    raw['metrics'][1]['conditions'][0]['value']=100000
    recipe=Recipe.model_validate(raw)
    before=recipe.model_dump()
    with pytest.raises(DomainError) as caught:
        preflight(recipe,2,request('실무 분석','sid','intermediate'))
    messages=[i['message'] for i in caught.value.validation_issues]
    assert len(messages)==2
    assert 'required judgments: uncertainty' in messages[0]
    assert 'no observed rows' in messages[1] and 'observed_table_ranges' in messages[1]
    assert recipe.model_dump()==before


def test_all_independent_live_structure_errors_are_in_one_repair():
    raw = example_recipe()
    raw['tables'][0]['groups'] = []
    raw['tables'][2]['columns'][0]['generator'] = {'kind':'foreign_key','table':'example_accounts'}
    raw['tables'][2]['columns'][1]['generator'] = {'kind':'foreign_key','table':'example_accounts'}
    raw['business_case']['rubric'] = raw.pop('rubric')
    original = deepcopy(raw)
    with pytest.raises(ValidationError):
        Recipe.model_validate(raw)
    messages = repair_messages(planning_messages(request('실무 분석','sid','intermediate'),[]),json.dumps(raw),[{'message':'first validation failure'}])
    feedback = json.loads(messages[-1]['content'])
    by_code = {i['code']:i for i in feedback['issues']}
    assert {'random_shape','primary_id','derived_generator','rubric_location'} <= set(by_code)
    assert by_code['derived_generator']['location'] == ['tables',2,'columns',1,'generator']
    assert 'source_column' in by_code['derived_generator']['message']
    assert by_code['rubric_location']['location'] == ['business_case','rubric']
    assert json.loads(messages[-2]['content']) == original == raw
    assert len([m for m in messages if m['role']=='assistant']) == 1


def test_valid_structure_has_no_synthetic_repair_errors():
    assert structural_issues(json.dumps(example_recipe())) == []
    assert structural_issues('invalid JSON') == []
    assert structural_issues('[]') == []


def test_invalid_sibling_log_join_reports_true_target_and_missing_filter():
    raw = example_recipe()
    raw['tables'].insert(2,dict(deepcopy(raw['tables'][1]),name='other_events'))
    raw['metrics'] = [{'name':'invalid_cohort','table':'example_summary','operation':'avg','column':'mean_duration',
        'joins':[{'table':'other_events','source_column':'account_id'}],
        'conditions':[{'column':'other_events.duration','operator':'gt','value':10}]}]
    context,issues = relational_context(json.dumps(raw))
    scope = context['metric_scopes'][0]
    assert scope['invalid_joins'][0]['actual_fk_target'] == 'example_accounts'
    assert scope['unavailable_references'] == ['other_events.duration']
    assert scope['next_allowed_joins'][0]['target_table'] == 'example_accounts'
    assert 'other_events.duration' not in scope['accessible_columns']
    assert issues and 'no fanout' in issues[0]['message']
    with pytest.raises(ValidationError):
        Recipe.model_validate(raw)


def test_valid_summary_profile_join_exposes_only_accessible_columns():
    raw = example_recipe()
    raw['metrics'] = [{'name':'mean','table':'example_summary','operation':'avg','column':'mean_duration',
        'joins':[{'table':'example_accounts','source_column':'account_id'}],
        'group_by':['example_accounts.platform']}]
    context,issues = relational_context(json.dumps(raw))
    assert issues == []
    scope = context['metric_scopes'][0]
    assert scope['invalid_joins'] == scope['unavailable_references'] == []
    assert 'example_accounts.platform' in scope['accessible_columns']
    assert 'example_events.duration' not in scope['accessible_columns']


def test_metric_shape_example_computes_real_profile_grouped_means():
    from da_agent.analytical_metrics import reference
    raw = example_recipe()
    raw['metrics'] = structure_examples()['metric_shapes']
    recipe = Recipe.model_validate(raw)
    rows = generate_rows(recipe,23)
    metric = recipe.metrics[1]
    result,query = reference(metric,{t.name:t for t in recipe.tables},rows)
    profiles = {r['id']:r['platform'] for r in rows['example_accounts']}
    for platform,mean in result['rows']:
        observations = [r['duration'] for r in rows['example_events'] if profiles[r['account_id']]==platform]
        assert mean == pytest.approx(sum(observations)/len(observations))
    assert 'JOIN "example_accounts"' in query
    assert '"example_events"."account_id" = "example_accounts"."id"' in query


def test_ratio_example_is_computed_at_original_event_grain():
    from da_agent.analytical_metrics import reference
    raw=example_recipe()
    raw['metrics']=structure_examples()['metric_shapes']
    recipe=Recipe.model_validate(raw)
    rows=generate_rows(recipe,23)
    result,_=reference(recipe.metrics[2],{t.name:t for t in recipe.tables},rows)
    profiles={r['id']:r['platform'] for r in rows['example_accounts']}
    for platform,ratio in result['rows']:
        observed=[r for r in rows['example_events'] if profiles[r['account_id']]==platform]
        assert ratio==pytest.approx(sum(r['duration']>=30 for r in observed)/len(observed))


def test_unsupported_ratio_generator_feedback_preserves_original_draft():
    raw=example_recipe()
    raw['tables'][2]['columns'][2]['generator']={'kind':'aggregate','operation':'ratio',
        'source_column':'duration','value_type':'number','denominator_conditions':[]}
    before=deepcopy(raw)
    with pytest.raises(ValidationError):
        Recipe.model_validate(raw)
    feedback=json.loads(repair_messages(planning_messages(request('실무 분석','sid','intermediate'),[]),
        json.dumps(raw),[{'message':'invalid operation'}])[-1]['content'])
    issue=next(i for i in feedback['issues'] if i['code']=='generator_metric_confusion')
    assert issue['location']==['tables',2,'columns',2,'generator']
    assert 'metrics[].operation=ratio' in issue['message'] and '분모' in issue['message']
    assert raw==before


def test_group_bound_error_does_not_hide_invalid_join_or_unobserved_retention():
    raw=example_recipe()
    raw['difficulty']='beginner'
    raw['title']='관측하지 못한 재방문 잔류율 분석'
    raw['tables'][1]['groups'][0]['count']=2001
    raw['metrics']=structure_examples()['metric_shapes'][:2]
    raw['metrics'][1]['joins'][0]['table']='example_summary'
    raw['business_case']['requirements']=[
        {'competency':'measurement','question':'관측된 집단별 평균 소요 시간을 계산하세요.',
         'evidence':[{'table':'example_events','columns':['duration']}],
         'metric_names':['mean_duration_by_platform'],'completion':'집단별 평균 소요 시간을 정확히 제시하세요.'},
        {'competency':'decision','question':'공개 관측의 차이를 근거로 다음 행동을 제안하세요.',
         'evidence':[{'table':'example_events','columns':['duration']}],
         'metric_names':['mean_duration_by_platform'],'completion':'관측 한계를 반영해 타당한 운영 행동을 제안하세요.'}]
    before=deepcopy(raw)
    with pytest.raises(ValidationError):
        Recipe.model_validate(raw)
    feedback=json.loads(repair_messages(planning_messages(request('실무 분석','sid','beginner'),[]),
        json.dumps(raw),[{'type':'less_than_equal','message':'less than or equal to 2000',
                          'location':['tables',1,'groups',0,'count']}])[-1]['content'])
    bound=next(i for i in feedback['issues'] if i['code']=='group_size_bound')
    assert '2001' in bound['message'] and '합성 표본' in bound['message']
    assert any(i['code']=='task_quality' and 'retention/churn' in i['message'] for i in feedback['issues'])
    assert any(i['code']=='metric_relation_or_definition' for i in feedback['issues'])
    scope=feedback['relationships']['metric_scopes'][1]
    assert scope['invalid_joins'][0]['actual_fk_target']=='example_accounts'
    assert '사용자가 직접 지정한 목표' in feedback['instruction']
    assert raw==before


@pytest.mark.parametrize('tables',[None,[{'name':{},'columns':[]}],[{'name':'bad','columns':None}]])
def test_incomplete_draft_cannot_crash_additional_quality_diagnostics(tables):
    raw=example_recipe()
    raw['tables']=tables
    assert quality_issues(json.dumps(raw))==[]


def test_mutually_exclusive_ratio_population_is_diagnosed_before_generating_rows():
    raw=example_recipe()
    raw['tables'][1]['columns'][2]={'name':'event_type','description':'관측 이벤트 종류',
        'generator':{'kind':'category','values':['start','complete']}}
    raw['tables'][1]['groups'][0]['count']=2001
    raw['metrics']=[{'name':'completion_rate','table':'example_events','operation':'ratio','purpose':'analysis',
        'conditions':[{'column':'event_type','operator':'eq','value':'complete'}],
        'denominator_conditions':[{'column':'example_events.event_type','operator':'eq','value':'start'}]}]
    before=deepcopy(raw)
    _,issues=relational_context(json.dumps(raw))
    conflict=next(i for i in issues if i['code']=='ratio_population_conflict')
    assert conflict['location']==['metrics',0,'conditions',0]
    assert '행 수나 난수 범위' in conflict['message'] and 'unsupported' in conflict['message']
    assert raw==before
    raw['metrics'][0]['conditions'][0]['value']='start'
    _,issues=relational_context(json.dumps(raw))
    assert not any(i['code']=='ratio_population_conflict' for i in issues)


def test_quality_link_failure_is_reported_with_primary_key_failure():
    raw = example_recipe()
    raw['difficulty'] = 'beginner'
    raw['tables'][2]['columns'].pop(0)
    raw['metrics'] = structure_examples()['metric_shapes']
    raw['business_case']['requirements'] = [
        {'competency':'measurement','question':'전체 관측 계정 수를 확인하세요.',
         'evidence':[{'table':'example_summary','columns':['account_id']}],
         'metric_names':['observed_accounts'],'completion':'관측된 계정 수를 확인하세요.'}]
    before = deepcopy(raw)
    feedback = json.loads(repair_messages(planning_messages(request('실무 분석','sid','beginner'),[]),
        json.dumps(raw),[{'message':'primary id required'}])[-1]['content'])
    codes = {i['code'] for i in feedback['issues']}
    assert {'primary_id','requirement_metric_link','required_competencies'} <= codes
    link = next(i for i in feedback['issues'] if i['code']=='requirement_metric_link')
    assert link['location'] == ['business_case','requirements',0,'metric_names']
    assert 'mean_duration_by_platform' in link['message'] and 'purpose=analysis' in link['message']
    assert raw == before


def test_all_missing_advanced_judgments_are_reported_together():
    raw = example_recipe()
    issues = requirement_issues(json.dumps(raw),'advanced')
    missing = next(i for i in issues if i['code']=='required_competencies')
    assert 'alternatives' in missing['message'] and 'confounding' in missing['message']
    assert requirement_issues('{bad','beginner') == []
    assert requirement_issues('[]','beginner') == []


def test_table_grouping_does_not_hide_missing_metric_comparison_join_and_control():
    raw=bot_recipe()
    raw['difficulty']='advanced'
    metric=raw['metrics'][-1]
    metric.pop('group_by');metric.pop('joins')
    raw['tables'][-1]['group_by']=['account_id']
    raw['business_case']['requirements'].append({
        'competency':'confounding','question':'같은 플랫폼 안에서 계정 활동량을 비교하세요.',
        'evidence':[{'table':'accounts','columns':['platform']}],
        'metric_names':['platform_actions'],'completion':'동일한 플랫폼에서 활동 차이를 비교하세요.',
        'judgment':{'method':'conditional_comparison','control_columns':['accounts.platform']}})
    before=deepcopy(raw)
    issues=requirement_issues(json.dumps(raw),'advanced')
    codes={i['code'] for i in issues}
    assert {'analysis_group_comparison','analysis_fk_comparison','confounding_group_comparison'}<=codes
    assert any('tables[].group_by' in i['message'] and 'metrics' in i['message'] for i in issues)
    assert raw==before
    metric['joins']=[{'table':'accounts','source_column':'account_id'}]
    metric['group_by']=['accounts.platform','interval_cv']
    fixed={i['code'] for i in requirement_issues(json.dumps(raw),'advanced')}
    assert not ({'analysis_group_comparison','analysis_fk_comparison','confounding_group_comparison'} & fixed)


def test_unsupported_retention_feedback_points_to_remaining_claim_fields():
    raw=bot_recipe()
    raw['goal']='관측되지 않은 리텐션을 분석합니다.'
    raw['business_case']['decision']='이 자료에서 재접속을 유도할 운영 전략을 정합니다.'
    before=deepcopy(raw)
    issues=quality_issues(json.dumps(raw))
    locations=[i['location'] for i in issues if i['code']=='unsupported_retention_claim']
    assert locations==[['goal'],['business_case','decision']]
    assert raw==before
    raw['goal']=bot_recipe()['goal']
    raw['business_case']['decision']=bot_recipe()['business_case']['decision']
    assert quality_issues(json.dumps(raw))==[]


def test_category_average_is_identified_at_exact_derived_column():
    raw = example_recipe()
    raw['tables'][1]['columns'][2]['generator'] = {'kind':'category','values':['fast','slow']}
    issues = structural_issues(json.dumps(raw))
    issue = next(i for i in issues if i['code']=='aggregate_numeric')
    assert issue['location'] == ['tables',2,'columns',2,'generator']
    assert 'example_events.duration' in issue['message'] and 'category' in issue['message']
    assert 'count' in issue['message']
    with pytest.raises(ValidationError):
        Recipe.model_validate(raw)


def test_alignment_input_omits_inactive_null_fields_losslessly():
    from da_agent.adaptive_tasks import alignment_messages
    recipe=Recipe.model_validate(bot_recipe())
    payload=json.loads(alignment_messages(request('실무 분석','sid','intermediate'),recipe)[-1]['content'])
    compact=payload['private_generation_recipe']
    assert Recipe.model_validate(compact)==recipe
    assert len(json.dumps(compact)) < len(json.dumps(recipe.model_dump()))
    assert compact['metrics']==recipe.model_dump(exclude_none=True)['metrics']
    gen=compact['tables'][0]['columns'][0]['generator']
    assert gen['kind']=='id' and 'source_column' not in gen and 'minimum' not in gen
