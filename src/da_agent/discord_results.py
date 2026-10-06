"""Readable, public-only submission cards; no model call or score rewriting."""
import hashlib
from datetime import datetime, timezone
from .errors import DomainError


REPORT_LABELS = {'question': '분석 질문', 'findings': '발견한 사실', 'hypothesis': '가설',
                 'alternatives': '대안 설명', 'quality': '데이터 신뢰성', 'limitations': '한계',
                 'action': '우선 대응', 'next_checks': '후속 검증', 'report_text': '제출 보고서'}


def submission_summary(entry):
    result = entry['result']
    if result.get('held'):
        return '제출·평가 기록을 저장했습니다. 평가 보류: ' + str(result.get('reason') or '판정 가능한 근거를 확인해야 합니다.')
    return f"제출·평가 기록을 저장했습니다. 평가 점수: {result.get('total', '미정')}/100 · 사람 검토 대기."


def build_submission(document, evaluation_id=None):
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
            add(REPORT_LABELS.get(key, '보고서 · ' + str(key)), value)
    answers = report.get('followup_answers', [])
    if answers:
        add('업무 담당자 후속 질문에 대한 답변', '\n\n'.join(f"답변 {i}\n{a.get('text', '')}" for i, a in enumerate(answers, 1)))
    for row in result.get('criteria', []):
        criterion = criteria.get(row['id'], {'name': row['id'], 'weight': 0})
        grade = row.get('grade')
        status = '판정 보류' if grade is None else f'등급 {grade}/4'
        body = ['평가 근거', str(row.get('reason') or row.get('held_reason') or result.get('reason') or '근거 확인 필요'),
                '', '다음 개선 행동', str(row.get('improvement') or '판정 가능한 근거를 보완한 뒤 다시 평가하세요.')]
        if row.get('evidence_refs'):
            body.extend(['', '인용한 근거'])
            for ref in row['evidence_refs']:
                kind, _, ident = ref.partition(':')
                body.append({'report': '제출 보고서 버전', 'execution': '저장 조회 ID', 'message': '과제 대화 ID'}.get(kind, kind) + ': ' + ident)
        add(f"{criterion['name']} · {status} · 비중 {criterion['weight']}%", '\n'.join(body))
    weak = (result.get('recommendation') or {})
    if isinstance(weak, dict):
        names = [criteria[k]['name'] for k in weak.get('practice_criteria', []) if k in criteria]
        add('다음 연습 제안', '우선 연습: ' + (' · '.join(names) if names else ('평가에 필요한 근거 보완' if result.get('held') else '현재 기준을 다른 과제에서도 적용하기')))
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
    return dict(evaluation_id=entry['id'], report_id=report['id'], report_version=report['version'],
                session_id=document['session_id'], guild_id=document['guild_id'], owner_user_id=document['owner_user_id'],
                thread_id=document.get('thread_id'), completed=document.get('state') == 'completed' and not result.get('held'),
                post_name=name[:100], cards=cards)


def card_marker(submission, index):
    digest = hashlib.sha256((submission['session_id'] + ':' + submission['evaluation_id']).encode()).hexdigest()[:20]
    return f"결과 {digest} · 카드 {index + 1}/{len(submission['cards'])}"


def legacy_card_marker(submission, index):
    """Recognize already posted cards while upgrading the visible footer."""
    return f"DA-Result · {submission['session_id']} · {submission['evaluation_id']} · {index}"
