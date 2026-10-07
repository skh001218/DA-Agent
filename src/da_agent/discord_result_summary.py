"""Extract a compact, public-only forum view without a model call.

These are bounded excerpts of saved submissions and assessments, not a new
evaluation. Full cards remain the authoritative input to the existing PDF.
"""
import re
from decimal import Decimal, InvalidOperation


LABELS = {
    '결론': 'conclusion', '종합 결론': 'conclusion', '핵심 결론': 'conclusion',
    '최종 결론': 'conclusion', '요약': 'conclusion', '분석 결론': 'conclusion',
    '핵심 근거': 'findings', '발견한 사실': 'findings', '분석 결과': 'findings',
    '주요 결과': 'findings', '근거': 'findings', '결과': 'findings',
    '분석 질문': 'question', '가설': 'hypothesis', '대안 설명': 'alternatives',
    '데이터 신뢰성': 'quality', '해석 한계': 'limitations', '한계': 'limitations',
    '우선 대응': 'action', '다음 행동': 'action', '권고': 'action', '제안': 'action',
    '후속 검증': 'next_checks',
}
SECTION = re.compile(
    r'(?:^|\s)(?:\#{1,6}\s*)?(?:\d+[.)]\s*)?(?:\*\*)?('
    + '|'.join(re.escape(label) for label in sorted(LABELS, key=len, reverse=True))
    + r')(?:\*\*)?[ \t]*(?:[:：][ \t]*|\r?\n)')


def excerpt(value, limit):
    """Keep an explicit ellipsis when a saved sentence does not fit."""
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def sentences(value):
    return [part.strip(' \t-*•') for part in re.split(r'(?<=[.!?。])\s+|\n+', str(value or ''))
            if part.strip(' \t-*•')]


def short_sentence(value, limit):
    parts = sentences(value)
    return next((part for part in parts if len(part) <= limit), parts[0] if parts else '')


def prose_excerpts(value):
    """Select unchanged metric/comparison sentences; IDs remain in full PDF."""
    parts = [re.sub(r'저장\s*조회\s+[0-9a-fA-F-]{36}\s*에서\s*', '', part)
             for part in sentences(value)]
    metrics = [part for part in parts if re.search(r'\d+(?:\.\d+)?\s*%|=\s*\d', part)]
    conclusion = next((part for part in metrics if re.search(r'높|낮|증가|감소|차이|순위', part)),
                      metrics[0] if metrics else parts[0] if parts else '')
    evidence = [part for part in metrics if part != conclusion][:3]
    action = next((part for part in parts if re.search(r'우선|먼저|권고|제안', part)
                   and re.search(r'한다\.?$|하세요\.?$|하겠습니다\.?$', part)), '')
    return conclusion, evidence, action


def report_sections(content):
    text = content.get('report_text', '')
    matches = list(SECTION.finditer(text)) if isinstance(text, str) else []
    sections = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        key = LABELS[match[1]]
        sections.setdefault(key, []).append(text[match.end():end].strip())
    parsed = {key: '\n'.join(values) for key, values in sections.items()}
    # Explicit fields take priority over prose, never expose unknown fields.
    for key in set(LABELS.values()):
        if isinstance(content.get(key), str) and content[key].strip():
            parsed[key] = content[key].strip()
    return parsed


def hold_reason(result):
    failures = {'api_rate_limited': '평가 공급자 호출 한도 또는 요청 빈도 제한',
                'api_unavailable': '평가 공급자 응답 불가', 'api_key_invalid': '평가 공급자 인증 실패',
                'api_permission_denied': '평가 공급자 접근 권한 부족'}
    failure = result.get('provider_failure') or {}
    if failure:
        return failures.get(failure.get('reason'), '평가 공급자 호출 실패')
    if result.get('profile_score_hold'):
        return '문제 유형의 반복 품질 검증 미통과'
    return result.get('individual_held_reason') or result.get('reason') or '판정 가능한 평가 근거 확인 필요'


def analysis_summary(task, report, result):
    content = report.get('content', {})
    sections = report_sections(content)
    prose_conclusion, prose_evidence, prose_action = prose_excerpts(content.get('report_text'))
    findings = sections.get('findings', '')
    conclusion = sections.get('conclusion') or findings
    if conclusion:
        conclusion = (sentences(conclusion)[0] if sections.get('conclusion') else
                      short_sentence(conclusion, 72))
    else:
        conclusion = '제출 발췌: ' + (prose_conclusion or '저장된 결론 없음 · 전체 PDF 확인')
    # A separate saved limitation must not disappear when findings are clipped.
    limitation = sections.get('limitations')
    if limitation:
        conclusion = excerpt(conclusion, 72) + ' · 한계: ' + excerpt(short_sentence(limitation, 40), 40)
    evidence = [item.strip(' -*•') for item in re.split(r'\n+|[,;]\s+', findings)
                if item.strip(' -*•')][:3]
    if not evidence:
        evidence = prose_evidence
    rows = result.get('criteria') or []
    graded = [row for row in rows if isinstance(row.get('grade'), (int, float))]
    strong = max(graded, key=lambda row: row['grade'], default={})
    weak = min(graded, key=lambda row: row['grade'], default={})
    strength = short_sentence(strong.get('reason', ''), 90) if strong.get('grade', 0) >= 3 else ''
    improvement_parts = sentences(weak.get('improvement', ''))
    improvement = improvement_parts[-1] if improvement_parts else ''
    errors = (result.get('arithmetic_verification') or {}).get('errors') or []
    held = bool(result.get('held'))
    if errors:
        next_action = errors[0].get('improvement') or '확인된 계산 오류의 주장·계산·결론을 수정하세요.'
    elif held:
        next_action = ('평가 공급자 상태를 확인한 뒤 다시 평가하세요.' if result.get('provider_failure') else
                       '문제 유형 검증이 통과한 뒤 다시 평가하세요.' if result.get('profile_score_hold') else
                       result.get('next_action') or '보류 이유를 확인하고 해결된 뒤 다시 평가하세요.')
    else:
        action_parts = sentences(sections.get('action'))
        next_action = action_parts[0] if action_parts else prose_action or improvement
        if not next_action:
            recommendation = result.get('recommendation')
            next_action = recommendation if isinstance(recommendation, str) else '전체 평가에서 다음 개선 행동을 확인하세요.'
    return dict(title=task.get('title', '분석 결과'), conclusion=conclusion,
                evidence=evidence, strength=strength, improvement=improvement,
                next_action=next_action, held=held, score=result.get('total'),
                hold_reason=hold_reason(result) if held else '',
                warning=f'확인된 계산 오류 {len(errors)}건 · 수정 필요' if errors else '',
                practice='analysis')


def sql_summary(task, attempt, result):
    rows = result.get('criteria') or []
    passed = [row for row in rows if row.get('status') == '충족']
    weak = next((row for row in rows if row.get('status') != '충족'), None)
    stored = attempt.get('full_result') or {}
    data = stored.get('rows') or []
    complete = bool(stored.get('result_complete'))
    evidence = [f"{'전체' if complete else '수집'} 실행 결과 {len(data)}행"
                + (' · 일부 결과' if not complete else '')]
    columns = [col.get('name', '') if isinstance(col, dict) else str(col)
               for col in stored.get('columns', [])]
    for values in data[:2]:
        row = dict(zip(columns, values))
        if {'numerator', 'denominator', 'completion_rate'} <= row.keys():
            rate = row['completion_rate']
            try:
                rate = f'{Decimal(str(rate)) * 100:.2f}%' if rate is not None else 'NULL'
            except (InvalidOperation, ValueError):
                rate = str(rate)
            group = ' '.join(str(row[name]) + ('주차' if name == 'week' else '')
                             for name in ('week', 'channel') if name in row)
            evidence.append((group + ': ' if group else '') + f"{row['numerator']}/{row['denominator']} = {rate}")
        else:
            evidence.append(' · '.join(f'{name}: {value}' for name, value in list(row.items())[:3]))
    held = bool(result.get('held'))
    if held:
        action = '보류 이유를 확인하고 해결된 뒤 다시 평가하세요.'
        conclusion = 'SQL 판정 보류 · 저장된 실행 결과와 설명은 전체 PDF에서 확인하세요.'
    elif weak:
        action = f"{weak.get('name', '미충족 항목')}의 평가 근거를 확인하고 SQL·검산 설명을 보완하세요."
        conclusion = f"검증 항목 {len(passed)}/{len(rows)}개 충족 · {weak.get('name', '미충족 항목')} 보완 필요"
    else:
        action = '현재 조건을 충족한 SQL을 다른 문제에 적용하세요.'
        conclusion = f'준비된 검증 데이터에서 {len(passed)}/{len(rows)}개 항목을 충족했습니다.'
    return dict(title=task.get('title', 'SQL 결과'), practice='sql', conclusion=conclusion,
                evidence=evidence, held=held, score=None,
                assessment=f'{len(passed)}/{len(rows)}개 충족 · 검증 데이터 기준',
                strength=passed[0].get('reason', '') if passed else '',
                improvement=weak.get('reason', '') if weak else result.get('limitation', ''),
                hold_reason=weak.get('reason', '판정 근거 확인 필요') if held and weak else '',
                next_action=action or '보류 이유를 확인하고 해결된 뒤 다시 평가하세요.',
                warning='일부 실행 결과 · 전체 결과 확인 필요' if not complete else '')


def legacy_summary(submission):
    """Compact fallback for already exported public fixtures, never new facts."""
    from .discord_pdf import plain_text
    cards = submission.get('cards') or []
    first = cards[0] if cards else {}
    report = '\n'.join(plain_text(c.get('description', '')) for c in cards
                       if c.get('pdf_kind') == 'report' or c.get('title', '').startswith('제출 보고서'))
    if not report:
        report = plain_text(first.get('description', ''))
    summary = analysis_summary({'title': plain_text(first.get('description', '')).split('\n')[0] or '제출 결과'},
        {'content': {'report_text': report}}, {'held': first.get('held'), 'total': first.get('score')})
    if submission.get('practice') == 'sql':
        summary.update(practice='sql', score=None, assessment='저장된 SQL 평가 · 상세 PDF 확인')
    return summary


def render_summary(summary):
    """Escape once, then bound each field including Discord escape characters."""
    from .discord_transport import safe_chunks

    def clip(value, limit):
        escaped = safe_chunks(re.sub(r'\s+', ' ', str(value or '')).strip(), limit=100000)[0]
        if len(escaped) <= limit:
            return escaped
        end = limit - 1
        if (len(escaped[:end]) - len(escaped[:end].rstrip('\\'))) % 2:
            end -= 1
        return escaped[:end].rstrip() + '…'

    held = summary.get('held')
    score = summary.get('score')
    assessment = '평가 보류 · 점수 미표시' if held else (
        summary.get('assessment') if summary.get('practice') == 'sql' else
        f'{score:g}/100 · 모델 평가 · 사람 검토 대기' if isinstance(score, (float, int)) else
        '점수 미확정 · 사람 검토 대기')
    body = ['**결론**', clip(summary.get('conclusion') or '저장된 결론 없음 · 전체 PDF 확인', 120),
            '', '**핵심 근거**']
    evidence = summary.get('evidence') or []
    body += ['• ' + clip(value, 90) for value in evidence[:3]] or ['• 선택된 근거 없음 · 전체 PDF 확인']
    body += ['', '**평가**', clip(assessment or '저장된 평가 · 전체 PDF 확인', 60)]
    if held:
        body.append('보류 이유: ' + clip(summary.get('hold_reason') or '판정 근거 확인 필요', 90))
    else:
        body.append('잘한 점: ' + clip(summary.get('strength') or '확인된 강점은 전체 평가에서 확인하세요.', 90))
        body.append('개선점: ' + clip(summary.get('improvement') or '전체 평가에서 개선점을 확인하세요.', 90))
    if summary.get('warning'):
        body.append('⚠ ' + clip(summary['warning'], 90))
    body += ['', '**다음 행동**', clip(summary.get('next_action') or '전체 평가에서 다음 행동을 확인하세요.', 120)]
    description = '\n'.join(body)
    # The per-field bounds guarantee this even for escaped multibyte input.
    assert len(description) <= 900
    return clip(summary.get('title') or '제출 결과', 160), description
