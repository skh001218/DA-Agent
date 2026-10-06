"""Direct learner SQL: public contracts, independent oracle and bounded checks.

Reference SQL and fixture schemas never enter public task/model context.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

from .errors import DomainError

TEMPLATE = '이 틀을 복사해 SQL을 작성한 뒤 이 메시지에 답장하세요. 수정도 전체 SQL을 새 답장으로 보내세요.\n```sql\n-- 여기에 풀이 SQL을 작성하세요.\n```'
CRITERIA = [('syntax', '구문·실행'), ('calculation', '계산·출력'),
            ('duplicates', '중복 처리'), ('period', '기간·관측'), ('denominator', '분모·누락')]
CASES = ('main', 'duplicates', 'period', 'denominator')


def extract_sql(text):
    if not isinstance(text, str) or len(text) > 1900:
        raise DomainError('sql_length', '코드 블록과 설명을 포함해 1,900자 이내로 한 번에 제출하세요. 나누어 실행하지 않습니다.')
    if text.count('```') != 2:
        raise DomainError('sql_block', '닫힌 sql 코드 블록 하나로 전체 풀이를 제출하세요.')
    match = re.search(r'```sql\s*\n([\s\S]*?)```', text, flags=re.IGNORECASE)
    if not match:
        raise DomainError('sql_block', '코드 블록의 언어를 sql로 지정하고 다음 줄부터 SQL을 작성하세요.')
    source = match.group(1).strip()
    # Parsing comments alone yields no executable statement. Do not make an attempt.
    if not re.sub(r'/\*[\s\S]*?\*/|--[^\n]*', '', source).strip():
        raise DomainError('sql_empty', '빈 틀입니다. 주석 아래에 풀이 SQL을 작성하세요.')
    return source


def make_task(analysis_task):
    task = deepcopy(analysis_task)
    level = task['difficulty']
    task.update(practice='sql', title='SQL · 신규 가입자 3단계 완료율', version='discord-sql-v1')
    fields = ['denominator', 'numerator', 'completion_rate']
    if level != 'beginner':
        fields.insert(0, 'channel')
    if level == 'advanced':
        fields.insert(0, 'week')
    task['sql_contract'] = dict(version='sql-tutorial-v1', columns=fields, tolerance='0.000001',
        ordering='unordered', numerator='관측 기간 안에 3단계를 완료한 고유 신규 가입자',
        denominator='과제 가입 기간의 모든 고유 신규 가입자 (도전하지 않은 가입자 포함)',
        empty='전체 집계 분모 0은 NULL 비율. 그룹 집계는 가입자가 있는 그룹만 출력.',
        criteria=[dict(id=i, name=n) for i, n in CRITERIA])
    grouping = {'beginner': '전체를 한 행으로', 'intermediate': '유입 채널별로',
                'advanced': '가입 주차(1 또는 2)와 유입 채널별로'}[level]
    task['objective'] = (f'신규 가입자의 튜토리얼 3단계 완료율을 {grouping} 계산하는 SELECT를 직접 작성하세요. '
        '완료율은 0~1 비율이며 분모 0은 NULL입니다. 도전하지 않은 가입자도 분모에 포함합니다. '
        '완료 이벤트는 가입 기간 시작 이상, 관측 종료 미만(UTC)만 인정하고 재도전을 중복 집계하지 않습니다. '
        '출력 열 이름과 순서: ' + ', '.join(fields) + '. 수와 비율은 숫자형, channel은 문자형입니다.')
    if level == 'beginner':
        task['objective'] += ' denominator는 전체 가입자 수, numerator는 완료한 가입자 수입니다. 나눗셈의 정수 절삭을 주의하세요.'
    if level == 'advanced':
        task['objective'] += ' week는 과제 시작일부터 7일 단위로 구분합니다. 중복과 기간 경계를 스스로 검산하세요.'
    return task


def fixture_rows(task, case):
    from .discord_education import fixture_dataset
    # Base fixture has no sql_fixture marker, preventing recursion.
    base = {k: v for k, v in task.items() if k != 'sql_fixture'}
    rows = fixture_dataset(base)
    start = date.fromisoformat(task['period']['start'])
    end = date.fromisoformat(task['period']['end'])
    first = datetime.combine(start, datetime.min.time(), timezone.utc)
    stop = datetime.fromisoformat(task['period']['observation_end']).replace(tzinfo=timezone.utc)
    if case == 'duplicates':
        rows['tutorial_attempts'] = rows['tutorial_attempts'] * 3
        rows['sessions'] *= 4
    elif case == 'period':
        rows['users'].extend([(101, start - timedelta(days=1), 'ads'), (102, end, 'organic'),
                              (103, start, 'ads'), (104, end - timedelta(days=1), 'organic')])
        rows['tutorial_attempts'].extend([(101, 3, True, first), (102, 3, True, first),
            (103, 3, True, first - timedelta(seconds=1)), (104, 3, True, stop),
            (103, 2, True, first), (104, 3, False, stop - timedelta(seconds=1))])
    elif case == 'denominator':
        if task['difficulty'] == 'beginner':
            return dict(users=[], tutorial_attempts=[], sessions=[])
        rows = dict(users=[(201, start, 'organic'), (202, start + timedelta(days=7), 'ads'),
                           (203, start, 'organic')], tutorial_attempts=[(203, 3, False, first)], sessions=[])
    return rows


def expected(task, rows):
    start, end = (date.fromisoformat(task['period'][k]) for k in ('start', 'end'))
    event_start = datetime.combine(start, datetime.min.time(), timezone.utc)
    stop = datetime.fromisoformat(task['period']['observation_end']).replace(tzinfo=timezone.utc)
    completed = {u for u, step, done, at in rows['tutorial_attempts']
                 if step == 3 and done and event_start <= at < stop}
    groups = {(): []} if task['difficulty'] == 'beginner' else {}
    for uid, joined, channel in rows['users']:
        if start <= joined < end:
            key = () if task['difficulty'] == 'beginner' else (channel,)
            if task['difficulty'] == 'advanced':
                key = ((joined - start).days // 7 + 1, channel)
            groups.setdefault(key, []).append(uid)
    output = []
    for key, users in groups.items():
        den = len(set(users)); num = len(set(users) & completed)
        output.append(list(key) + [den, num, Decimal(num) / den if den else None])
    return output


def reference_sql(task):
    """Private executable oracle cross-checked with independent Python counts."""
    start, end = (task['period'][k] for k in ('start', 'end'))
    stop = task['period']['observation_end']
    fields = []
    if task['difficulty'] == 'advanced':
        fields.append(f"((u.signup_date - DATE '{start}') / 7 + 1) AS week")
    if task['difficulty'] != 'beginner':
        fields.append('u.channel')
    groups = len(fields)
    fields.extend(['COUNT(DISTINCT u.user_id) AS denominator', 'COUNT(DISTINCT c.user_id) AS numerator',
        'COUNT(DISTINCT c.user_id)::numeric / NULLIF(COUNT(DISTINCT u.user_id), 0) AS completion_rate'])
    query = ('SELECT ' + ', '.join(fields) + ' FROM users u LEFT JOIN '
        f"(SELECT DISTINCT user_id FROM tutorial_attempts WHERE step=3 AND completed=true AND attempt_at >= '{start}'::timestamptz AND attempt_at < '{stop}'::timestamptz) c ON c.user_id=u.user_id "
        f"WHERE u.signup_date >= DATE '{start}' AND u.signup_date < DATE '{end}'")
    return query + (' GROUP BY ' + ','.join(str(i + 1) for i in range(groups)) if groups else '')


def matches(task, result, rows):
    if not result.get('result_complete') or result.get('status') != 'success':
        return False
    if [c['name'] for c in result['columns']] != task['sql_contract']['columns']:
        return False
    numeric = {'int2', 'int4', 'int8', 'numeric', 'float4', 'float8'}
    if any(c.get('type') not in numeric for c in result['columns'] if c['name'] != 'channel'):
        return False
    actual = result['rows']
    if len(actual) != len(rows):
        return False
    def equal(a, b):
        if a is None or b is None:
            return a is b
        if isinstance(b, (int, Decimal)):
            try:
                value = Decimal(str(a))
                return value.is_finite() and abs(value - Decimal(b)) <= Decimal('0.000001')
            except (InvalidOperation, ValueError):
                return False
        return a == b
    remaining = list(actual)
    for row in rows:
        for index, candidate in enumerate(remaining):
            if len(candidate) == len(row) and all(equal(a, b) for a, b in zip(candidate, row)):
                remaining.pop(index)
                break
        else:
            return False
    return True


def run_full(runner, sid, schema, sql):
    preview = runner.execute(sid, schema, sql, allowed_tables={'users', 'tutorial_attempts', 'sessions'})
    stored = runner.get(sid, preview['execution_id'])
    return preview, stored['result']


def validate_problem(runner, sid, task, checks):
    for case in checks:
        _, result = run_full(runner, sid, case['schema_name'], reference_sql(task))
        if not matches(task, result, expected(task, fixture_rows(task, case['case']))):
            raise DomainError('sql_problem_invalid', 'SQL 문제의 기준 계산을 검증하지 못했습니다. 과제를 공개하지 않습니다.')


def evaluate(runner, sid, task, checks, attempt):
    checks_result = []
    held = False
    for check in checks:
        if check['case'] == 'main':
            result = attempt['full_result']
        else:
            _, result = run_full(runner, sid, check['schema_name'], attempt['sql'])
        complete = result.get('status') == 'success' and result.get('result_complete')
        # No execution/partial result is evidence of a semantic error.
        held = held or not complete
        checks_result.append(dict(case=check['case'], complete=bool(complete),
            matched=matches(task, result, expected(task, fixture_rows(task, check['case']))) if complete else None))
    rows = []
    for ident, name in CRITERIA:
        tests = checks_result if ident == 'calculation' else [c for c in checks_result if c['case'] == ident]
        status = '충족' if ident == 'syntax' else ('판정 보류' if any(not c['complete'] for c in tests)
                 else '충족' if all(c['matched'] for c in tests) else '보완 필요')
        rows.append(dict(id=ident, name=name, status=status,
            reason='실제 읽기 전용 실행 성공' if ident == 'syntax' else '고정 검증 데이터의 전체 결과 비교',
            evidence_refs=[f"execution:{attempt['execution_id']}"]))
    return dict(practice='sql', rubric_version=task['sql_contract']['version'], held=held,
        reason='실행 실패 또는 결과 수집 제한으로 의미 판정을 보류합니다.' if held else None,
        total=None, criteria=rows, checks=checks_result, human_review='pending',
        limitation='준비된 검증 데이터 범위의 결과 비교이며 모든 SQL의 동치 증명·독립 역량·학습 효과 판정은 아닙니다.',
        recommendation='보완 항목을 수정해 전체 SQL을 새 답장으로 제출하세요. 모두 충족했다면 다른 문제에 적용하세요.')


def summary(entry):
    result = entry['result']
    return 'SQL 평가 · ' + ('판정 보류' if result['held'] else ' / '.join(c['name'] + ': ' + c['status'] for c in result['criteria']))


def submission(document, evaluation_id=None):
    from .discord_transport import safe_chunks
    entry = next((e for e in document['evaluations'] if e['id'] == evaluation_id), None) if evaluation_id else document['evaluations'][-1]
    if not entry:
        raise DomainError('result_missing', '저장된 SQL 평가가 없습니다.')
    attempt = next(a for a in document['sql_attempts'] if a['execution_id'] == entry['execution_id'])
    cards = []
    for title, body in [('SQL 연습 결과', document['task']['title'] + '\n' + summary(entry) + '\n' + entry['result']['limitation']),
            ('문제', document['task']['objective']), ('제출 SQL', attempt['sql']),
            ('도움·정답 노출', 'SQL 노출: ' + entry['result'].get('sql_exposure', '미상') + '\n평가 시점 도움 이력: ' + str(len(entry['result'].get('help_history', [])))),
            ('다음 연습', entry['result']['recommendation'])]:
        for chunk in safe_chunks(body):
            cards.append(dict(title=title, description=chunk))
    cards[0].update(score=None, held=entry['result']['held'])
    for criterion in entry['result']['criteria']:
        cards.append(dict(title=criterion['name'] + ' · ' + criterion['status'],
            description=safe_chunks(criterion['reason'] + '\n선택한 제출 SQL의 실행과 고정 검증 데이터를 근거로 합니다. 실행 ID: ' + attempt['execution_id'])[0]))
    return dict(session_id=document['session_id'], evaluation_id=entry['id'], owner_user_id=document['owner_user_id'],
        guild_id=document['guild_id'], completed=not entry['result']['held'], practice='sql',
        post_name=f"SQL 연습 · {document['session_id'][:8]} · v{entry['version']}", cards=cards,
        evidence_results=[dict(execution_id=attempt['execution_id'], columns=attempt['full_result']['columns'],
            rows=attempt['full_result']['rows'], total_row_count=attempt['full_result'].get('total_row_count'),
            truncated=not attempt['full_result'].get('result_complete'), placement='report', practice='sql')],
        message_sources=[])
