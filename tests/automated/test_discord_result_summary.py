"""Compact presentation must preserve saved semantics, status, and full PDF input."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
discord = pytest.importorskip('discord')

from da_agent.discord_result_summary import analysis_summary, sql_summary, render_summary, report_sections
from da_agent.discord_results import build_submission
from da_agent.discord_forum import ResultForumPublisher, embed_bytes, MAX_EMBED_BYTES
from test_discord_results import document


def result(**changes):
    return dict(total=75, held=False, criteria=[
        dict(id='strong', grade=4, reason='분자와 분모를 구분했습니다.', improvement='현재 기준을 유지하세요.'),
        dict(id='weak', grade=1, reason='비교 기간이 다릅니다.', improvement='같은 기간으로 다시 비교하세요.')],
        **changes)


def test_structured_fields_override_prose_and_do_not_publish_unknown_private_fields():
    report = {'content': {'report_text': '결론: 오래된 결론. 발견한 사실: 오래된 수치.',
        'findings': 'ads 13/19 완료, organic 17/21 완료', 'conclusion': '채널별 완료율이 다릅니다.',
        'limitations': '관측만으로 원인을 확정하지 않습니다.', 'action': '주차별 구성을 비교하세요.',
        'private_answer': 'NEVER_PUBLISH'}}
    before = deepcopy(report)
    summary = analysis_summary({'title': '채널 분석', 'private': 'NEVER_PUBLISH'}, report, result())
    _, body = render_summary(summary)
    assert '채널별 완료율이 다릅니다' in body and '관측만으로 원인을 확정하지 않습니다' in body
    assert 'ads 13/19' in body and '분자와 분모' in body and '같은 기간' in body
    assert summary['next_action'] == '주차별 구성을 비교하세요.'
    assert '오래된' not in body and 'NEVER_PUBLISH' not in body
    assert report == before


@pytest.mark.parametrize('text', [
    '발견한 사실: ads 68.42%, organic 80.95%. 한계: 인과 효과 미확정. 우선 대응: 로그 점검.',
    '**발견한 사실**: ads 68.42%, organic 80.95%. **한계**: 인과 효과 미확정. **우선 대응**: 로그 점검.',
    '## 발견한 사실\nads 68.42%, organic 80.95%.\n## 한계\n인과 효과 미확정.\n## 우선 대응\n로그 점검.',
])
def test_labelled_old_reports_keep_decimals_and_limits(text):
    sections = report_sections({'report_text': text})
    assert sections['action'] == '로그 점검.'
    summary = analysis_summary({'title': '분석'}, {'content': {'report_text': text}}, result())
    _, body = render_summary(summary)
    assert '68.42%' in body and '80.95%' in body and '인과 효과 미확정' in body
    assert '로그 점검' in body


def test_unlabelled_report_is_explicit_excerpt_not_invented_conclusion():
    summary = analysis_summary({}, {'content': {'report_text': '검토할 자료를 정리했습니다. 원인은 아직 모릅니다.'}}, result())
    _, body = render_summary(summary)
    assert summary['conclusion'].startswith('제출 발췌: ')
    assert '선택된 근거 없음' in body


def test_prose_metrics_skip_test_preface_preserve_causal_uncertainty_and_action():
    text = ('전체 흐름 검증용 보고서. 합성 자료다. '
        '저장 조회 b1d4b9a6-c62e-4570-9e58-ff5acc0e53ad에서 커뮤니티 50%, 광고 40%다. '
        '커뮤니티가 10%p 높지만 채널의 인과 효과로 확정할 수 없다. '
        '경험 많음은 0/50=0%, 광고 20/20=100%다. '
        '미완료 규모가 큰 광고 집단을 우선 조사한다.')
    summary = analysis_summary({}, {'content': {'report_text': text}}, result())
    _, body = render_summary(summary)
    assert '10%p 높지만 채널의 인과 효과로 확정할 수 없다' in body
    assert '커뮤니티 50%' in body and '20/20' in body
    assert '검증용 보고서' not in body and 'b1d4b9a6' not in body
    assert summary['next_action'] == '미완료 규모가 큰 광고 집단을 우선 조사한다.'


def test_held_supplier_failure_hides_stale_score_and_does_not_blame_learner():
    saved = result()
    saved.update(held=True, total=95, reason='LEARNER_MISSING',
                 provider_failure={'reason': 'api_rate_limited', 'provider_diagnostic': {'raw': 'SECRET'}})
    summary = analysis_summary({}, {'content': {'findings': '완료율 75%'}}, saved)
    _, body = render_summary(summary)
    assert '평가 보류' in body and '/100' not in body and '잘한 점' not in body
    assert '호출 한도' in body and '공급자 상태' in body
    assert 'LEARNER_MISSING' not in body and 'SECRET' not in body


def test_arithmetic_error_takes_priority_over_generic_next_practice():
    saved = result()
    saved['arithmetic_verification'] = {'errors': [{'improvement': '30/40을 75%로 수정하세요.'}]}
    summary = analysis_summary({}, {'content': {'action': '다른 과제를 연습하세요.'}}, saved)
    _, body = render_summary(summary)
    assert '계산 오류 1건' in body and summary['next_action'] == '30/40을 75%로 수정하세요.'
    assert saved['total'] == 75


def test_profile_hold_has_its_own_reason_and_no_score():
    saved = result()
    saved.update(held=True, profile_score_hold=True)
    _, body = render_summary(analysis_summary({}, {'content': {}}, saved))
    assert '반복 품질 검증 미통과' in body and '/100' not in body
    assert '검증이 통과한 뒤' in body


def sql_inputs(complete=True):
    task = {'title': 'SQL 완료율'}
    attempt = {'full_result': {'result_complete': complete, 'total_row_count': 100,
        'columns': ['week', 'channel', 'numerator', 'denominator', 'completion_rate'],
        'rows': [[1, 'ads', 4, 5, '0.8'], [1, 'organic', 0, 0, None]]}}
    saved = dict(held=False, limitation='전체 SQL의 동치를 증명하지 않습니다.', criteria=[
        dict(id='syntax', name='구문·실행', status='충족', reason='실행 성공'),
        dict(id='output', name='계산·출력', status='충족', reason='전체 결과 일치')])
    return task, attempt, saved


def test_sql_uses_saved_counts_and_null_not_an_analysis_score():
    task, attempt, saved = sql_inputs()
    _, body = render_summary(sql_summary(task, attempt, saved))
    assert '2/2개 충족' in body and '/100' not in body
    assert '4/5 = 80.00%' in body and '0/0 = NULL' in body
    saved['criteria'][1].update(status='보완 필요', reason='분모 확인 필요')
    summary = sql_summary(task, attempt, saved)
    assert '계산·출력' in summary['next_action'] and '1/2개' in summary['conclusion']


def test_partial_sql_is_not_labelled_as_full_result():
    task, attempt, saved = sql_inputs(False)
    _, body = render_summary(sql_summary(task, attempt, saved))
    assert '수집 실행 결과 2행' in body and '일부 결과' in body
    assert '전체 실행 결과' not in body and '100행' not in body


def test_long_escaped_text_keeps_three_evidence_items_and_wire_budget():
    doc = document()
    sub = build_submission(doc)
    giant = '@everyone **긴 한글과 이모지😀** ' * 1000
    sub['forum_summary'].update(title=giant, conclusion=giant, evidence=[giant] * 100,
        strength=giant, improvement=giant, next_action=giant, warning=giant, assessment=giant)
    before = deepcopy(sub)
    embed = ResultForumPublisher(None).post_embeds(sub, NS(display_name=giant))[0]
    assert len(embed.description) <= 900 and embed.description.count('• ') == 3
    assert embed_bytes([embed]) <= MAX_EMBED_BYTES
    assert '@everyone' not in embed.description and '…' in embed.description
    assert sub == before


def test_summary_uses_requested_evaluation_report_not_latest_saved_report():
    doc = document()
    doc['reports'][0]['content'] = {'findings': '첫 보고서의 결론 75%'}
    doc['reports'].append(dict(id='later', version=2, content={'findings': '다른 보고 0%'}))
    sub = build_submission(doc, 'evaluation')
    assert '첫 보고서' in sub['forum_summary']['conclusion']
    assert '다른 보고' not in sub['forum_summary']['conclusion']
