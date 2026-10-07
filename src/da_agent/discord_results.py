"""Readable, public-only submission cards; no model call or score rewriting."""
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from .errors import DomainError


REPORT_LABELS = {'question': '분석 질문', 'findings': '발견한 사실', 'hypothesis': '가설',
                 'alternatives': '대안 설명', 'quality': '데이터 신뢰성', 'limitations': '한계',
                 'action': '우선 대응', 'next_checks': '후속 검증', 'report_text': '제출 보고서'}


def submission_summary(entry):
    result = entry['result']
    if result.get('held'):
        return '제출·평가 기록을 저장했습니다. 평가 보류: ' + str(result.get('reason') or '판정 가능한 근거를 확인해야 합니다.')
    errors = result.get('arithmetic_verification', {}).get('errors', [])
    warning = f' 계산 불일치 {len(errors)}건 · 수정 필요.' if errors else ''
    return f"제출·평가 기록을 저장했습니다. 평가 점수: {result.get('total', '미정')}/100 · 사람 검토 대기.{warning} /report로 수정 보고를 시작하고 새 후속 답변 뒤 재제출할 수 있습니다."


def build_submission(document, evaluation_id=None):
    if document.get('practice') == 'sql':
        from .discord_sql_practice import submission
        return submission(document, evaluation_id)
    from .discord_transport import safe_chunks
    from .discord_education import growth_observation
    entries = document.get('evaluations', [])
    entry = next((e for e in entries if e['id'] == evaluation_id), None) if evaluation_id else (entries[-1] if entries else None)
    if not entry:
        raise DomainError('result_missing', '저장된 제출 평가가 없습니다. 보고와 후속 답변을 마친 뒤 /submit하세요.')
    report = next((r for r in document['reports'] if r['id'] == entry['report_id']), None)
    if not report:
        raise DomainError('result_report_missing', '평가에 연결된 보고서를 확인할 수 없습니다. 기록을 보존했습니다.')
    task, result = document['task'], entry['result']
    criteria = {c['id']: c for c in task['rubric']['criteria']}
    cards = []

    def add(title, body):
        # Escape only source material. Embed titles provide the section hierarchy.
        for index, chunk in enumerate(safe_chunks(body), 1):
            cards.append(dict(title=title if index == 1 else f'{title} · 이어서 {index}', description=chunk))

    submitted_at = datetime.fromisoformat(entry['at']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    add('제출 결과 요약', '\n'.join([
        task.get('title', '분석 훈련'), submission_summary(entry),
        f"보고 버전: {report['version']} · 제출 시각 (UTC): {submitted_at}",
        f"과제 ID: {document['session_id']}",
        '업무 목표: ' + task.get('objective', ''),
        '평가는 공개 기준에 따른 모델 판정이며 학습 효과를 확정하지 않습니다.',
        '이 게시글은 DA-Result 채널에 접근할 수 있는 회원에게 공개됩니다.',
    ]))
    cards[0]['score'] = result.get('total')
    cards[0]['held'] = bool(result.get('held'))
    for key, value in report.get('content', {}).items():
        if isinstance(value, str) and value.strip():
            start = len(cards)
            add(REPORT_LABELS.get(key, '보고서 · ' + str(key)), value)
            for card in cards[start:]:
                card['pdf_kind'] = 'report'
    answers = report.get('followup_answers', [])
    if answers:
        start = len(cards)
        add('업무 담당자 후속 질문에 대한 답변', '\n\n'.join(f"답변 {i}\n{a.get('text', '')}" for i, a in enumerate(answers, 1)))
        for card in cards[start:]:
            card['pdf_kind'] = 'followup'
    profile=result.get('quality_profile')
    if profile:
        lines=['문제 유형 반복 검증: '+('통과' if profile['status']=='eligible' else '미통과 · 점수 보류'),
               '공개 문제 정의·난이도·모델·평가 코드가 일치하는 검증을 요구합니다.',
               '분석 피드백은 모델의 관측이며 사람 검토 완료를 뜻하지 않습니다.']
        lines.extend(profile.get('reasons',[]))
        if result.get('individual_held_reason'): lines.append('개별 평가 보류 이유: '+result['individual_held_reason'])
        if profile['status']!='eligible': lines.append('검증이 통과할 때까지 같은 보고의 /submit 재시도는 추가 모델 호출 없이 저장된 피드백을 보여줍니다.')
        add('문제 유형 품질 상태','\n'.join(lines))
    quality = result.get('quality_validation')
    if quality:
        lines = ['감점 근거 검증: ' + ('통과' if quality['status'] == 'passed' else '미통과 · 점수 보류'),
                 '검증 정책: ' + quality['version'], '검증 범위: 공개 기준·인용·중복·확인된 산술 오류 연결',
                 '모든 의미적 판단의 정답이나 사람 검토 완료를 뜻하지 않습니다.']
        repair = result.get('response_repair', {})
        repair_status={'failed':'재검증 실패','revalidated':'재검증 통과','not_attempted':'미시도'}
        lines.append('자동 수정: ' + str(repair.get('attempts', 0)) + '/1회 · ' + repair_status.get(repair.get('status'),str(repair.get('status','불필요'))))
        failure=result.get('provider_failure',{})
        if failure:
            reasons={'api_rate_limited':'호출 한도 또는 요청 빈도 제한','api_unavailable':'공급자 응답 불가',
                     'api_key_invalid':'공급자 인증 실패','api_permission_denied':'공급자 접근 권한 부족'}
            lines.append('공급자 실패: '+reasons.get(failure.get('reason'),'평가 호출 실패'))
            lines.append('공급자 상태를 확인한 뒤 재시도하세요. 문제 유형의 검증 보류가 함께 있으면 검증 통과 후 새 평가가 가능합니다.')
        for issue in quality.get('issues', []):
            lines.append(issue['criterion_id'] + ': ' + issue['detail'])
        if result.get('held') and quality['status']!='passed':
            lines.append('평가 응답을 확인하지 못한 시스템 보류입니다. 학습자0점이 아닙니다. 근거를 확인한 뒤 /submit로 재시도하세요.')
        elif result.get('profile_score_hold'):
            lines.append('개별 감점 근거 검사는 통과했지만 문제 유형의 반복 품질 검증이 미통과하여 점수를 보류합니다.')
        add('평가 신뢰성 확인', '\n'.join(lines))
    verification = result.get('arithmetic_verification')
    if verification:
        status = {'errors_found': '계산 불일치 확인', 'checked_supported_claims': '지원하는 명확한 주장 검산', 'not_checked': '검산할 근거 또는 지원 형식 부족'}
        lines = [status.get(verification['status'], verification['status']), verification['scope'],
                 '검산 정책: ' + verification['version'],
                 '확인한 주장: ' + str(len(verification['checks'])) + '개 · 확인된 오류: ' + str(len(verification['errors'])) + '개',
                 '미검산 표현은 정답 확인을 의미하지 않습니다.']
        for error in verification['errors']:
            lines.extend(['', '보고 주장: ' + error['claim'], '검산 근거: ' + error['reason'],
                          '다음 수정: ' + error['improvement'], '저장 조회 근거: ' + ', '.join(error['evidence_refs'])])
        for note in verification['notes']:
            lines.append('확인 한계: ' + note['reason'])
        add('실행 근거 검산', '\n'.join(lines))
    comparison = entry.get('revision_comparison')
    if comparison:
        lines = [f"보고 v{comparison['previous_report_version']} → v{comparison['report_version']}",
                 '이전 평가 ID: ' + comparison['previous_evaluation_id'], comparison['note']]
        lines.append(f"점수: {comparison.get('previous_total') if comparison.get('previous_total') is not None else '보류'} → {comparison.get('total') if comparison.get('total') is not None else '보류'}")
        for change in comparison['criteria']:
            label = criteria.get(change['id'], {}).get('name', change['id'])
            before = '보류' if change['before'] is None else str(change['before'])
            after = '보류' if change['after'] is None else str(change['after'])
            lines.append(f'{label}: {before} → {after}')
        lines.append(f"확인된 검산 오류: {comparison['errors_before'] if comparison['errors_before'] is not None else '이전 미기록'} → {comparison['errors_after']}")
        lines.append('검산 기준이 다른 경우 점수·오류 수를 같은 기준의 개선으로 단정하지 않습니다.')
        add('이전 제출과 수정 비교', '\n'.join(lines))
    feedback = entry.get('revision_feedback')
    if feedback:
        lines = [feedback['note'], '같은 정책·근거로 재검산한 주장만 해결로 표시합니다.']
        lines.append('같은 계산 결함을 반복한 여러 문장은 하나의 오류로 묶어 비교합니다.')
        for key, label in (('resolved','해결한 오류'),('remaining','남은 오류'),('new','새로 확인한 오류')):
            values = feedback[key]
            lines.append(label + ': ' + str(len(values)) + '건')
            for error in values:
                lines.extend(['- ' + error['claim'], '  검산: ' + error['reason']])
        lines.append('추가 확인: ' + (' / '.join(feedback['unverified']) if feedback['unverified'] else '지원하는 검산 범위 안에서 별도 미확인 항목 없음'))
        if feedback['remaining'] or feedback['new']:
            action = '확인된 오류의 주장·계산·결론을 수정하세요.'
        elif feedback['unverified']:
            action = '미확인 주장의 분자·분모·기간과 실행 근거를 보완하세요.'
        elif result.get('profile_score_hold'):
            action = '지원하는 검산 범위의 오류 수정은 확인했습니다. 유형의 반복 품질 검증 통과 후 /submit로 다시 평가하세요.'
        else:
            action = '지원하는 검산 범위의 오류 수정은 확인했습니다. 분석의 적용 한계와 다음 확인 계획을 검토하세요.'
        lines.append('다음 행동: ' + action)
        add('수정 내용 피드백', '\n'.join(lines))

    for row in result.get('criteria', []):
        criterion = criteria.get(row['id'], {'name': row['id'], 'weight': 0})
        grade = row.get('grade')
        status = '판정 보류' if grade is None else f"{'모델 관측 등급' if result.get('profile_score_hold') else '등급'} {grade}/4"
        improvement=str(row.get('improvement') or '보류 이유를 확인하고 해결된 뒤 다시 평가하세요.')
        if grade is None and result.get('provider_failure'):
            improvement='공급자 상태를 확인한 뒤 다시 평가하세요. 공급자 실패를 학습자 근거 부족으로 감점하지 않습니다.'
        body = ['평가 근거', str(row.get('reason') or row.get('held_reason') or result.get('reason') or '근거 확인 필요'),
                '', '다음 개선 행동', improvement]
        pdf_chunks = safe_chunks('\n'.join(body))

        if row.get('evidence_refs'):
            body.extend(['', '인용한 근거'])
            for ref in row['evidence_refs']:
                kind, _, ident = ref.partition(':')
                body.append({'report': '제출 보고서 버전', 'execution': '저장 조회 ID', 'message': '과제 대화 ID'}.get(kind, kind) + ': ' + ident)
        start = len(cards)
        add(f"{criterion['name']} · {status} · 비중 {criterion['weight']}%", '\n'.join(body))
        for chunk_index, card in enumerate(cards[start:]):
            card['pdf_description'] = pdf_chunks[chunk_index] if chunk_index < len(pdf_chunks) else ''
        cards[-1]['pdf_evidence_refs'] = list(dict.fromkeys(row.get('evidence_refs', [])))
    weak = (result.get('recommendation') or {})
    if isinstance(weak, dict):
        names = [criteria[k]['name'] for k in weak.get('practice_criteria', []) if k in criteria]
        fallback = ('유형의 반복 품질 검증 대기 · 현재 분석 근거와 적용 한계 검토' if result.get('profile_score_hold') else
                    '평가에 필요한 근거 보완' if result.get('held') else '현재 기준을 다른 과제에서도 적용하기')
        add('다음 연습 제안', '우선 연습: ' + (' · '.join(names) if names else fallback))
    elif isinstance(weak, str):
        add('다음 연습 제안', weak)
    growth = entry.get('growth') or growth_observation(document.get('learning', []))
    body = [str(growth.get('status', '성장 판단 자료 부족')),
            '비교에 사용할 수 있는 관측: ' + str(growth.get('count', growth.get('observed_count', 0))) + '개',
            '제외된 관측: ' + str(len(growth.get('excluded', []))) + '개',
            '사람이 과제 간 비교 가능성을 확인하고 도움 전 근거를 확보해야 성장 판단이 가능합니다.']
    for key, values in growth.get('observations', {}).items():
        body.append(f"{criteria.get(key, {}).get('name', key)}: {values['baseline']} → {values['followup']} (변화 {values['delta']:+d})")
    add('학습 관측과 판단 한계', '\n'.join(body))
    name = f"{task.get('title', '분석 훈련')} · v{report['version']} · {document['session_id'][:8]}-{entry['id'][:8]}"
    # Export only successful results selected as evidence for this report version.
    # SQL, unrelated queries and private task/source records stay in the session.
    selected = set(report.get('evidence_refs', []))
    cited = {ref.partition(':')[2] for row in result.get('criteria', [])
             for ref in row.get('evidence_refs', []) if ref.startswith('execution:')}
    evidence_results = []
    for execution in document.get('executions', []):
        ident = execution.get('execution_id', execution.get('id'))
        stored = execution.get('result', {})
        if ident not in selected | cited or stored.get('status') != 'success':
            continue
        evidence_results.append(dict(
            execution_id=ident,
            placement='report' if ident in selected else 'evaluation',
            columns=[{key: deepcopy(col[key]) for key in ('name', 'type') if key in col}
                     if isinstance(col, dict) else str(col) for col in stored.get('columns', [])],
            rows=deepcopy(stored.get('rows', [])),
            truncated=bool(stored.get('truncated')), total_row_count=stored.get('total_row_count'),
            conditions={key: deepcopy(value) for key, value in execution.get('conditions', {}).items()
                        if key in {'metric', 'group_by', 'start', 'end', 'event_start', 'event_end',
                                   'timezone', 'step', 'unit', 'numerator', 'denominator'}},
        ))
    cited_messages = {ref.partition(':')[2] for row in result.get('criteria', [])
                      for ref in row.get('evidence_refs', []) if ref.startswith('message:')}
    message_sources = []
    for message in document.get('messages', []):
        if message.get('id') not in cited_messages or message.get('role') not in {'user', 'stakeholder'}:
            continue
        answer_number = next((i for i, answer in enumerate(answers, 1)
                              if answer.get('message_id') == message['id']), None)
        location = ('followup' if answer_number else
                    'summary' if message.get('text') == task.get('objective') else 'quote')
        message_sources.append(dict(id=message['id'], text=message.get('text', ''),
                                    location=location, answer_number=answer_number))
    return dict(evaluation_id=entry['id'], report_id=report['id'], report_version=report['version'],
                session_id=document['session_id'], guild_id=document['guild_id'], owner_user_id=document['owner_user_id'],
                thread_id=document.get('thread_id'), completed=document.get('state') == 'completed' and not result.get('held'),
                post_name=name[:100], cards=cards, evidence_results=evidence_results,
                message_sources=message_sources,
                summary_context={
                    'title': task.get('title', '분석 훈련'),
                    'period': {key: deepcopy(value) for key, value in task.get('period', {}).items()
                               if key in {'start', 'end', 'description'}},
                    'timezone': task.get('timezone', ''),
                })


def card_marker(submission, index):
    digest = hashlib.sha256((submission['session_id'] + ':' + submission['evaluation_id']).encode()).hexdigest()[:20]
    return f"결과 {digest} · 카드 {index + 1}/{len(submission['cards'])}"


def legacy_card_marker(submission, index):
    """Recognize already posted cards while upgrading the visible footer."""
    return f"DA-Result · {submission['session_id']} · {submission['evaluation_id']} · {index}"
