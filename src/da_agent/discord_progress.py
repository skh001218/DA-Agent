"""Public progress reports count completed generation steps, never elapsed time."""

TOTAL_STEPS = 3
COMPLETED_STEPS = {'accepted': 0, 'planning': 0, 'preparing_data': 1, 'validating': 2, 'ready': 3}
LABELS = {
    'accepted': '출제 요청 저장', 'planning': '문제 설계·검토 중',
    'preparing_data': '자료 생성·검산 중', 'validating': '문제 공개 준비 중',
    'ready': '문제 출제 완료', 'failed': '출제 실패', 'cancelled': '출제 취소',
    'interrupted': '출제 중단', 'needs_clarification': '출제 조건 답변 대기',
}


def snapshot(generation):
    """Project only public counters; model drafts and errors stay out of callbacks."""
    status = generation['status']
    completed = COMPLETED_STEPS.get(status, generation.get('completed_steps'))
    return {'status': status, 'completed_steps': completed}


def progress_message(generation):
    progress = snapshot(generation)
    completed = progress['completed_steps']
    label = LABELS.get(progress['status'], '출제 상태 확인 중')
    if type(completed) is not int or not 0 <= completed <= TOTAL_STEPS:
        return '출제 단계 완료율: 확인 불가 · ' + label
    percent = round(completed / TOTAL_STEPS * 100)
    text = f'출제 단계 완료율: {percent}% ({completed}/{TOTAL_STEPS}단계 완료) · {label}'
    if progress['status'] in {'accepted', 'planning', 'preparing_data', 'validating'}:
        text += '\n단계 완료 기준이며 남은 시간 비율은 아닙니다. /end로 취소할 수 있습니다.'
    return text
