"""Fixed SQL problem layout using saved public facts and dictionary attachments."""
from string import Template


HEADER = Template('''📌 $title
난이도: $difficulty · 연습 유형: SQL

🎯 1. 문제 목표
$goal

📚 2. 사용할 데이터
첨부한 데이터 사전 이미지를 누르면 확대할 수 있습니다.
$collection''')

BODY = Template('''🔎 3. 계산 조건
$conditions

📤 4. 출력 형식
$output

📝 5. 제출 방법
1. 아래에 제공된 sql 코드 블록 틀을 복사해 풀이를 작성하세요.
2. 틀 안내 메시지에 답장해 실행하세요. 수정할 때도 전체 SQL을 새 답장으로 보내세요.
3. 실행 결과를 확인한 뒤 /submit로 최종 평가를 요청하세요.
• 코드 블록과 설명을 포함해 1,900자 이내로 제출하세요.
$extra
• /help로 데이터 사전·평가 기준·개념 도움을 확인하세요.
• 답장 수신이 제한되면 /sqlrun text:코드블록을 사용하세요.

문제 정보
$metadata''')


def _items(values):
    return '\n'.join('• ' + str(value) for value in values if value)


def sql_intro_messages(document):
    """Place existing dictionary images between the two returned messages.

    Legacy tasks retain their complete public objective without guessing how
    free-form sentences map to fields. Rendering never changes saved contracts.
    """
    task = document['task']
    contract = task['sql_contract']
    sections = task.get('sql_intro_sections')
    if not sections:
        output = ['열 이름과 순서: ' + ', '.join(contract['columns'])]
        if contract.get('ordering') == 'unordered':
            output.append('행 순서는 무관합니다.')
        sections = dict(goal=['공개 조건을 충족하는 SELECT를 직접 작성하세요.'],
                        conditions=[task['objective']], output=output, extra=[])
    period = task.get('period', {})
    conditions = list(sections['conditions'])
    if contract['version'] == 'sql-generated-v1':
        if period.get('description'):
            conditions.insert(0, '관측 기간: ' + period['description'])
    else:
        conditions[:0] = [
            f"가입 기간: {period.get('start', '미정')} 이상 ~ {period.get('end', '미정')} "
            + ('미만' if period.get('end_exclusive', True) else '이하'),
            f"시간: {task.get('timezone', 'UTC')} · 완료 관측 종료: {period.get('observation_end', '미정')} 미만",
        ]
    extra = list(sections.get('extra', []))
    if contract.get('verification_required') and not extra:
        extra.append('SQL 블록 밖에 중복·기간/관측 경계·분모/NULL의 검산 방법을 설명하세요. '
                     '수행하지 않은 검산은 계획이라고 표시하세요.')
    metadata = ['데이터 버전: ' + document['data_version']]
    if contract['version'] != 'sql-generated-v1':
        metadata.append('원본 SQL 노출: ' + document.get('sql_exposure', '미상'))
    collection = task.get('quality_information', {}).get('collection', '')
    return [HEADER.substitute(title=task['title'],
                difficulty={'beginner':'초급', 'intermediate':'중급', 'advanced':'고급'}.get(task.get('difficulty'), '미상'),
                goal='\n'.join(sections['goal']), collection=collection).rstrip(),
            BODY.substitute(conditions=_items(conditions), output=_items(sections['output']),
                            extra=_items(extra), metadata=_items(metadata)).replace('\n\n• /help', '\n• /help')]
