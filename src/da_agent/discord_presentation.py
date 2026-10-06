"""Concise task introduction and on-demand public reference information."""
from datetime import date, timedelta


def task_intro(document):
    task = document['task']
    period = task['period']
    start = date.fromisoformat(period['start'])
    end = date.fromisoformat(period['end'])
    last = end - timedelta(days=1) if period.get('end_exclusive', True) else end
    ranges = []
    cursor = start
    while cursor <= last:
        week_end = min(cursor + timedelta(days=6), last)
        ranges.append(f'{len(ranges) + 1}주차: {cursor.isoformat()}~{week_end.isoformat()} 가입자')
        cursor = week_end + timedelta(days=1)
    return '\n'.join([
        '📌 ' + task['title'], '',
        task['objective'], '',
        '분석 대상', *ranges,
        '유입 채널: organic / ads',
        f"시간 기준: {task['timezone']} · 관측 종료: {period['observation_end']}", '',
        '이용 가능한 데이터',
        '가입자 정보, 단계별 도전·완료 기록, 접속 기록입니다. 재도전은 중복 기록될 수 있습니다.',
        task.get('quality_information', {}).get('collection', ''), '',
        '/query로 새 조회를 요청하세요. 봇 질문에는 답장·멘션 또는 /answer로 답해주세요.',
        '분석 결과와 대응 제안은 /report로 작성하세요.',
        '/tip command:명령어로 사용법과 예시를 확인할 수 있습니다.',
        '/question으로 모르는 용어를 물어보세요. 예: ads와 organic이 뭐야?',
        '/help로 도움을 받거나 데이터 사전·평가 기준·전체 명령을 확인할 수 있습니다.',
    ])


def reference_info(task, kind):
    if kind == 'data_dictionary':
        lines = ['데이터 사전']
        for name, entry in task['dictionary'].items():
            lines.extend([f"{name} — 한 행: {entry['unit']}", entry['description'],
                          '컬럼: ' + ', '.join(f'{key}: {value}' for key, value in entry['columns'].items())])
        return '\n'.join(lines)
    if kind == 'evaluation_criteria':
        rubric = task['rubric']
        lines = ['평가 기준']
        for criterion in rubric['criteria']:
            lines.extend([f"{criterion['name']} ({criterion['weight']}%)",
                          '필수: ' + criterion['required'], '핵심 오류: ' + criterion['core_error'],
                          '추가 검증: ' + criterion['advanced']])
        lines.extend(['등급: ' + ' / '.join(f'{grade} = {meaning}' for grade, meaning in rubric['grades'].items()),
                      f"통과 기준: {rubric['passing_grade']}등급 · 기준 버전: {rubric['version']}",
                      '점수에 반영하지 않음: ' + ', '.join(rubric['non_scoring']),
                      '같은 오류는 중복 감점하지 않습니다.',
                      '인정하는 한계: ' + ' / '.join(task.get('accepted_limits', []))])
        return '\n'.join(lines)
    if kind == 'commands':
        return '\n'.join(['전체 명령 안내', '/tip command:명령어 — 사용법과 예시 확인', '/training — 새 훈련 시작', '/query — 새 자연어 조회 요청', '/answer — 현재 봇 질문에 대한 답변', '/question — 용어당 최대 3줄로 뜻 설명',
            '/help — 도움 요청 또는 데이터 사전·평가 기준·전체 명령 확인',
            '/sql — 저장된 조회의 실행 SQL 확인', '/evidence — 조회를 보고 근거로 선택',
            '/report — 보고 작성·수정 (append로 긴 보고 이어 쓰기)',
            '/followup — 업무 담당자 후속 질문 답변', '/submit — 최종 제출과 평가',
            '/end — 중단·기록 보존', '/resume — 진행 과제 재개·완료 결과 열람', '/history — 내 연습 기록·결과 링크'])
    return None


COMMAND_TIPS = {
    'question': ('본인의 과제 스레드에서 모르는 게임 분석 용어를 물어봅니다. 용어당 최대 3줄로 답하며 분석 상태를 유지합니다.', 'text: 질문 (필수). 최대 5개 용어·500자. 기본 용어는 API 없이 설명하고 그 밖의 질문은 일일 한도 내 모델 호출.', '/question text:ads와 organic이 뭐야?'),
    'training': ('새 비공개 과제 스레드에서 훈련을 시작합니다. 부모 텍스트 채널에서 사용하세요.', 'topic: 주제, difficulty: 난이도, help_level: 도움 수준 (모두 선택)', '/training difficulty:intermediate'),
    'query': ('새 조회를 요청합니다. 기존 확인 질문에 답할 때는 답장·멘션 또는 /answer를 사용하세요.', 'text: 조회 요청 (필수)', '/query text:두 주의 채널별 튜토리얼 3단계 완료율을 비교해줘'),
    'answer': ('현재 봇 질문에 이어서 답합니다. 조회 조건·분석 이유·보고 후속 질문에 사용할 수 있습니다.', 'text: 답변 (필수)', '/answer text:과제 기간의 신규 가입 고유 사용자를 분모로 사용해주세요'),
    'help': ('개념·분석 방향·중간 피드백 또는 참고 정보를 확인합니다.', 'text: 질문 (선택), kind: 개념/분석 방향/중간 검토/데이터 사전/평가 기준/전체 명령 (선택)', '/help kind:데이터 사전'),
    'sql': ('성공한 저장 조회의 실제 실행 SQL을 보여줍니다.', 'execution_id: 조회 결과에 표시된 실행 ID (필수)', '/sql execution_id:실행ID'),
    'evidence': ('성공한 저장 조회를 보고 근거로 선택합니다. 보고 저장 전에 선택하세요.', 'execution_id: 조회 결과에 표시된 실행 ID (필수)', '/evidence execution_id:실행ID'),
    'report': ('보고를 작성하거나 수정합니다. 매번 새 버전으로 저장하며 이전 버전은 보존합니다.', 'text: 보고 내용 (필수), append: True면 최신 보고 뒤에 줄바꿈 후 추가. False 또는 생략하면 입력 내용만 저장. 첫 보고에서는 True여도 새로 작성합니다.', '/report text:분석 결과와 대응 제안…\n/report text:추가 검증과 한계… append:True\n내용을 모두 작성한 뒤 새 후속 질문에 답하고 /submit하세요.'),
    'followup': ('보고 작성 후 업무 담당자의 후속 질문에 답합니다. 답장·멘션 또는 /answer도 사용할 수 있습니다.', 'text: 답변 (필수)', '/followup text:채널별 비교와 로그 점검을 먼저 진행하겠습니다'),
    'submit': ('최신 보고를 최종 제출하고 평가를 요청합니다. 먼저 보고를 작성하고 후속 질문에 답해야 합니다.', '추가 입력 없음', '/submit'),
    'end': ('훈련을 중단하고 기록을 보존합니다. /resume으로 이어갈 수 있습니다.', '추가 입력 없음', '/end'),
    'resume': ('본인의 저장 과제를 불러옵니다. ID를 생략하면 같은 서버에서 최근 갱신된 과제를 선택합니다. 진행 과제는 재개·복구하고 완료 게시된 과제는 결과와 과거 대화 링크를 안내하며 보관·잠금을 유지합니다.', 'session_id: 특정 훈련 ID (선택)', '/resume\n/resume session_id:훈련ID'),
    'history': ('현재 서버의 본인 연습 기록을 5개씩 확인합니다. 생성일·상태·평가 점수·결과 링크와 재개 명령을 안내합니다.', 'page: 페이지 번호 (선택, 기본 1)', '/history\n/history page:2'),
    'tip': ('명령어의 사용법과 예시를 확인합니다. 과제 없이도 사용할 수 있고 분석 상태를 바꾸지 않습니다.', 'command: 명령어 이름 (선택). report와 /report 모두 가능.', '/tip command:report\n/tip command:/report\n/tip'),
}


def command_tip(command=''):
    name = command.strip().lower().lstrip('/')
    if not name:
        return '📖 명령어 사용법\n/tip의 command 옵션에 명령어를 입력하세요. 예: /tip command:report\n사용 가능한 명령어: ' + ', '.join('/' + key for key in COMMAND_TIPS)
    if name not in COMMAND_TIPS:
        return f'알 수 없는 명령어: {command.strip()}\n/tip으로 지원하는 명령어 목록을 확인하세요.'
    purpose, options, example = COMMAND_TIPS[name]
    return f'📖 /{name} 사용법\n{purpose}\n\n입력 옵션\n{options}\n\n사용 예시\n{example}'
