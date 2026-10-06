from copy import deepcopy
from da_agent.discord_education import representative_task, evaluate_report
from da_agent.discord_verification import verify_report
from da_agent.evaluation_metrics import verify_declared_metrics


def fixture(operation='sum'):
    task=representative_task()
    conditions=dict(metric='revenue',start='2026-09-01',end='2026-09-08',timezone='UTC',unit='payment',filters={},group_by='channel')
    metric=dict(id='revenue-total',label='총매출',operation=operation,unit='원',conditions=conditions,
                value_column='revenue',group_column='channel')
    execution=dict(execution_id='revenue-1',status='success',data_version=task['data_version'],conditions=conditions,
        result=dict(status='success',result_complete=True,truncated=False,columns=['channel','revenue'],rows=[['ads','320.00'],['organic','180.00']]))
    if operation=='ratio':
        metric.update(label='구매전환율',unit='%',numerator_column='buyers',denominator_column='users')
        execution['result'].update(columns=['channel','users','buyers'],rows=[['ads',10,3],['organic',90,9]])
    task['arithmetic_contract']={'version':'declared-metrics-v1','metrics':[metric]}
    return task,[execution]


def verify(text,operation='sum',mutation=None):
    task, executions=fixture(operation)
    if mutation: mutation(task,executions)
    report=dict(version=1,text=text,evidence_refs=['revenue-1'])
    return verify_declared_metrics(task,report,executions,verify_report(task,report,executions))


def test_new_revenue_type_sum_and_explicit_weighted_ratio():
    assert verify('총매출은500원입니다.')['checks'][0]['status']=='matched'
    assert verify('총매출은600원입니다.')['errors'][0]['expected']=='500.00'
    assert verify('구매전환율은12%.','ratio')['checks'][0]['status']=='matched'
    assert verify('구매전환율은20%.','ratio')['errors'][0]['expected']=='12'


def test_unsupported_ambiguous_failed_filtered_and_duplicate_results_are_not_errors():
    assert not verify('총매출은600원일 가능성이 있다.')['errors']
    assert not verify('총매출은600원가량으로 추정된다.')['checks']
    assert not verify('구매전환율은12%p.','ratio')['checks']
    for mutation in (
        lambda t,e:e[0]['result'].update(truncated=True),
        lambda t,e:e[0].update(status='error'),
        lambda t,e:e[0].update(data_version='old'),
        lambda t,e:e[0].update(conditions={**e[0]['conditions'],'filters':{'country':'KR'}}),
        lambda t,e:e[0]['result']['rows'].append(['ads',10]),
    ):
        result=verify('총매출은600원입니다.',mutation=mutation)
        assert not result['errors'] and result['status']=='not_checked'


def test_invalid_metric_contract_is_task_hold_without_provider_call():
    task,executions=fixture(); task['arithmetic_contract']['metrics'][0].pop('value_column')
    calls=[]
    result=evaluate_report(lambda context:calls.append(context),task,
                          dict(version=1,text='총매출600원',evidence_refs=['revenue-1']),[],executions,[])
    assert result['held'] and result['total'] is None and not calls
    assert result['quality_validation']['issues'][0]['code']=='metric_contract_invalid'


def test_repeated_execution_not_added_and_conflict_cannot_be_ignored():
    task,executions=fixture()
    duplicate=deepcopy(executions[0]); duplicate['execution_id']='revenue-2'; executions.append(duplicate)
    report=dict(version=1,text='총매출500원',evidence_refs=['revenue-1','revenue-2'])
    result=verify_declared_metrics(task,report,executions,verify_report(task,report,executions))
    assert result['checks'][0]['expected']=='500.00'
    duplicate['result']['rows'][0][1]='400'
    result=verify_declared_metrics(task,report,executions,verify_report(task,report,executions))
    assert result['status']=='not_checked' and not result['errors']
