from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from da_agent.discord_education import representative_task, evaluate_report
from da_agent.discord_verification import verify_report


def evidence():
    base = dict(metric='tutorial_rate', timezone='UTC', period_basis='explicit_dates', unit='user',
                numerator='completed_users', denominator='signup_users', step=3, filters={}, group_by='channel',
                event_start='2026-09-01', event_end='2026-10-01')
    return [dict(id=f'q{i}', execution_id=f'q{i}', status='success', data_version='v1',
                 conditions={**base, 'start': start, 'end': end},
                 result=dict(status='success', result_complete=True, truncated=False,
                             columns=['group_value', 'denominator', 'numerator', 'rate_percent'], rows=rows))
            for i, (start, end, rows) in enumerate([
                ('2026-09-01', '2026-09-08', [['ads', 5, 4, '80'], ['organic', 15, 12, '80']]),
                ('2026-09-08', '2026-09-15', [['ads', 15, 10, '66.6667'], ['organic', 5, 4, '80']])], 1)]


def check(text, executions=None, refs=None):
    report = {'version': 1, 'content': {'report_text': text}, 'evidence_refs': refs if refs is not None else ['q1', 'q2']}
    return verify_report(representative_task(), report, executions if executions is not None else evidence())


@pytest.mark.parametrize('text,kind', [
    ('1주차 전체 완료율은 90%.', 'completion_rate'),
    ('2주차 12/20=70%.', 'numerator'),
    ('전체 완료율은 10% 감소했다.', 'relative_change'),
    ('전체 완료율은 12.5%p 하락했다.', 'percentage_point_change'),
    ('1주차 채널 비율이 모두80%이지만 유입 구성만 바꾸면 전체70%가 된다.', 'composition_only'),
    ('구성 변화만으로 전체 하락을 완전히 설명한다.', 'composition_only'),
])
def test_wrong_assertions_are_computed_from_counts(text, kind):
    result = check(text)
    assert result['status'] == 'errors_found'
    assert any(e['kind'] == kind for e in result['errors'])
    assert all(e['evidence_refs'] == ['execution:q1', 'execution:q2'] for e in result['errors'])


@pytest.mark.parametrize('text', [
    '1주차 16/20=80%. 2주차 14/20=70%. 전체 완료율은10%p 하락했다.',
    '전체 완료율은12.5% 감소했다.',
    'ads 2주차 66.67%. organic 2주차 80%.',
    '1주차 채널 비율을 고정하고 구성만 바꾸면 전체80%가 된다.',
    '구성 변화만으로 전체 하락을 설명할 수 없다.',
    '구성 변화만으로 전체70%가 될까?',
    '가설: 구성 변화만으로 전체70%가 된다.',
    '“구성 변화만으로 전체70%가 된다”는 잘못된 해석이다.',
    '구성 변화만으로 전체 하락을 설명한다는 가설을 기각했다.',
    '2주차 비율을 고정해 구성만 바꾸면 전체76.67%다.',
])
def test_correct_limits_questions_and_other_reference_base_not_penalized(text):
    assert not check(text)['errors']


@pytest.mark.parametrize('mutation', ['incomplete', 'failed', 'different_event_window', 'different_denominator',
                                    'different_version', 'conflicting_repeat', 'zero_denominator'])
def test_unobservable_or_incomparable_data_not_scored_as_error(mutation):
    executions = evidence()
    if mutation == 'incomplete': executions[1]['result']['result_complete'] = False
    if mutation == 'failed': executions[1]['status'] = 'error'
    if mutation == 'different_event_window': executions[1]['conditions']['event_start'] = '2026-09-08'
    if mutation == 'different_denominator': executions[1]['conditions']['denominator'] = 'attempted_users'
    if mutation == 'different_version': executions[1]['data_version'] = 'v2'
    if mutation == 'conflicting_repeat':
        duplicate = deepcopy(executions[1]); duplicate['execution_id'] = 'q3'
        duplicate['result']['rows'][0][2] = 9; executions.append(duplicate)
    if mutation == 'zero_denominator':
        executions[1]['result']['rows'] = [['ads', 0, 0, None], ['organic', 0, 0, None]]
    result = check('구성 변화만으로 전체 하락을 완전히 설명한다.', executions, ['q1', 'q2', 'q3'])
    assert not result['errors'] and result['status'] == 'not_checked'


def test_only_report_selected_evidence_and_latest_content_checked():
    assert check('구성 변화만으로 전체 하락을 완전히 설명한다.', refs=['q1'])['status'] == 'not_checked'
    assert check('구성 변화만으로 전체 하락을 완전히 설명한다.', refs=[])['status'] == 'not_checked'
    duplicate = deepcopy(evidence()[0]); duplicate['execution_id'] = 'q3'
    assert len(check('1주차80%. 2주차70%.', evidence() + [duplicate], ['q1','q2','q3'])['checks']) == 2


def provider(grades=4):
    return SimpleNamespace(review=lambda messages: {'state':'completed', 'text':json.dumps({'criteria':[
        dict(id=c['id'], grade=grades, reason='모델은 상위 조건 충족으로 판단', improvement='다음 비교',
             evidence_refs=['report:1']) for c in representative_task()['rubric']['criteria']]})})


def test_model_perfect_score_capped_for_confirmed_error_once_and_inputs_preserved():
    task, executions = representative_task(), evidence()
    before = deepcopy(executions)
    result = evaluate_report(provider(), task, {'version':1, 'text':'구성 변화만으로 전체 하락을 완전히 설명한다.', 'evidence_refs':['q1','q2']}, [], executions, [])
    assert not result['held'] and result['total'] == 85
    grades = {r['id']: r for r in result['criteria']}
    assert grades['evidence_interpretation']['grade'] == 1
    assert grades['evidence_interpretation']['provider_grade'] == 4
    assert grades['evidence_interpretation']['deductions'][0]['source']=='deterministic_public_calculation'
    assert len(grades['evidence_interpretation']['deductions']) == 1
    assert grades['hypothesis_review']['grade'] == 4
    assert executions == before


def test_api_failure_keeps_system_hold_despite_detected_error():
    result = evaluate_report(SimpleNamespace(review=lambda messages: {'state':'error'}), representative_task(),
                             {'version':1, 'text':'구성 변화만으로 전체 하락을 완전히 설명한다.'}, [], evidence(), [])
    assert result['held'] and result['total'] is None
    assert result['arithmetic_verification']['errors']


def test_no_numeric_assertion_does_not_claim_full_verification():
    result = check('관측 결과를 참고해 로그 점검을 제안한다.')
    assert result['status'] == 'not_checked' and result['notes']
