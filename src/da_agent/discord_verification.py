"""Conservative arithmetic checks on selected, complete public query results.

This checks explicit supported assertions, not arbitrary prose or hidden causes.
All verdicts come from saved counts and conditions, never model-provided numbers.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
from copy import deepcopy
from .evaluation_quality import fingerprint

VERSION = 'discord-arithmetic-v1'
POLICY = ('선택한 완전한 조회의 두 주 신규 가입자 완료율·증감·1주차 비율을 고정한 구성만 변경 주장을 검산합니다. '
          '확인된 계산 불일치는 근거 해석 항목의 등급 상한1로만 반영합니다. '
          '다른 지표·모호한 표현·질문·가설·부정·인용과 2주차 비율을 기준으로 한 분해는 자동 검산 범위 밖입니다. '
          '미검산은 정답 확인이 아니며 시스템 보류는 점수로 바꾸지 않습니다.')
TOLERANCE = Decimal('0.06')  # percentage points: permits 66.7/66.67 rounding
SIGNATURE_KEYS = ('metric', 'timezone', 'period_basis', 'unit', 'denominator',
                  'numerator', 'step', 'event_start', 'event_end', 'filters')


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean count')
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError('Non-finite count')
    return value


def _sentences(text):
    return re.split(r'\n+|(?<=[.!?])\s+', text)


def _assertion(text):
    # Do not score proposed hypotheses, questions, quoted mistakes or retractions.
    return not re.search(r'[?？]|가설|가능|만약|기각|보류|대략|가량|추정|약\s*\d|확인\s*필요|설명할\s*수\s*없|설명하지\s*못|설명할\s*수\s*있을|아니|틀린|틀렸|잘못|않|못|[“”「」"‘’]', text)


def report_text(report):
    content = report.get('content', {})
    if isinstance(content, dict) and content:
        values = [v for v in content.values() if isinstance(v, str)]
    else:
        values = [report.get('text', '')]
    # Evaluate only this report and its own followups, not historic report messages.
    values += [a.get('text', '') for a in report.get('followup_answers', [])]
    return '\n'.join(v for v in values if isinstance(v, str))


def _ledger(task, report, executions, notes):
    selected = report.get('evidence_refs')
    selected = {str(x).removeprefix('execution:') for x in selected} if isinstance(selected, list) else None
    groups = {}
    for execution in executions:
        ident = str(execution.get('execution_id') or execution.get('id') or '')
        if not ident or (selected is not None and ident not in selected):
            continue
        result = execution.get('result', {})
        conditions = execution.get('conditions', {})
        if execution.get('status') not in {'success', 'succeeded'} or result.get('status') not in {'success', 'succeeded'} or result.get('result_complete') is not True or result.get('truncated'):
            notes.append({'execution_id': ident, 'reason': '성공·전체 수집 완료 근거가 아님'})
            continue
        if conditions.get('metric') != 'tutorial_rate' or conditions.get('unit') != 'user' or conditions.get('denominator') != 'signup_users' or conditions.get('numerator') != 'completed_users' or conditions.get('filters') != {} or conditions.get('group_by') not in (None, 'channel'):
            notes.append({'execution_id': ident, 'reason': '신규 가입자 완료율 전체/채널 검산 범위 밖'})
            continue
        required = ('start', 'end', 'timezone', 'period_basis', 'step', 'event_start', 'event_end')
        if any(not conditions.get(key) for key in required) or conditions['timezone'] != task.get('timezone') or not execution.get('data_version'):
            notes.append({'execution_id': ident, 'reason': '공개 기간·단위 계약을 확인할 수 없음'})
            continue
        try:
            names = [c['name'] if isinstance(c, dict) else c for c in result['columns']]
            records = [dict(zip(names, row)) for row in result['rows'] if len(row) == len(names)]
            if len(records) != len(result['rows']) or not records:
                raise ValueError('Rows missing')
            if result.get('total_row_count') is not None and result['total_row_count'] != len(records):
                raise ValueError('Rows missing')
            counts = {}
            for row in records:
                channel = row['group_value'] if conditions.get('group_by') else '전체'
                n, k = _number(row['denominator']), _number(row['numerator'])
                if channel in counts or n < 0 or k < 0 or k > n or n != n.to_integral_value() or k != k.to_integral_value():
                    raise ValueError('Invalid counts')
                counts[channel] = {'n': n, 'k': k}
            if not conditions.get('group_by') and len(counts) != 1:
                raise ValueError('Invalid aggregate')
            signature = json.dumps({key: conditions.get(key) for key in SIGNATURE_KEYS}, sort_keys=True)
            key = (conditions['start'], conditions['end'], signature, conditions.get('group_by'), execution.get('data_version'))
            prior = groups.get(key)
            if prior and prior['counts'] != counts:
                prior['conflict'] = True
            elif not prior:
                groups[key] = {'counts': counts, 'refs': [f'execution:{ident}'], 'conflict': False}
            else:
                prior['refs'].append(f'execution:{ident}')
        except (KeyError, ValueError, TypeError, InvalidOperation):
            notes.append({'execution_id': ident, 'reason': '검산할 분자·분모·전체 행을 확인할 수 없음'})
    return groups


def verify_report(task, report, executions):
    if task.get('generation_version'):
        return {'version':'generated-evidence-v1','status':'unverified','scope':'생성 과제의 임의 자연어 수치 주장은 자동 검산하지 않습니다.',
            'checks':[],'errors':[],'notes':[{'reason':'공개 조회의 실제 저장 결과를 평가 근거로 제공하며 미검산을 학습자 오류로 만들지 않습니다.'}]}
    result = {'version': VERSION, 'status': 'not_checked', 'checks': [], 'errors': [], 'notes': [],
              'scope': '선택한 완전한 신규 가입자 완료율 근거의 명확한 두 주 수치·증감·구성 확정 주장. 다른 지표·모호한 표현은 미검산.'}
    ledger = _ledger(task, report, executions, result['notes'])
    try:
        start = date.fromisoformat(task['period']['start'])
        periods = [(start.isoformat(), (start + timedelta(days=7)).isoformat()),
                   ((start + timedelta(days=7)).isoformat(), (start + timedelta(days=14)).isoformat())]
        if task['period']['end'] != periods[1][1]:
            raise ValueError('Not two complete weeks')
    except (KeyError, TypeError, ValueError):
        result['notes'].append({'reason': '공개 두 주 기간을 확인할 수 없음'})
        return result
    candidates = []
    if any(key[:2] in periods and value['conflict'] for key, value in ledger.items()):
        result['notes'].append({'reason': '같은 조건으로 저장된 조회의 분자·분모 충돌'})
        return result
    for key, first in ledger.items():
        if key[:2] != periods[0] or first['conflict']:
            continue
        second = ledger.get((*periods[1], *key[2:]))
        if second and not second['conflict']:
            candidates.append((key, first, second))
    # Prefer disjoint channel counts. Aggregate and channel queries are not added.
    candidates.sort(key=lambda candidate: candidate[0][3] != 'channel')
    if not candidates or len({(c[0][2], c[0][4]) for c in candidates}) != 1:
        result['notes'].append({'reason': '동일 조건의 비교 가능한 두 주 근거 없음 또는 조건이 여러 개임'})
        return result
    _, first, second = candidates[0]
    weeks = [first, second]
    for week in weeks:
        week['n'] = sum(c['n'] for c in week['counts'].values())
        week['k'] = sum(c['k'] for c in week['counts'].values())
        if not week['n']:
            result['notes'].append({'reason': '전체 분모가 0이므로 비율·증감 검산 보류'})
            return result
        week['rate'] = 100 * week['k'] / week['n']
    # Simultaneous aggregate/channel results must agree; conflicts cannot be ignored.
    for _, one, two in candidates[1:]:
        for expected, actual in zip(weeks, (one, two)):
            if (sum(c['n'] for c in actual['counts'].values()), sum(c['k'] for c in actual['counts'].values())) != (expected['n'], expected['k']):
                result['notes'].append({'reason': '같은 기간 전체·채널 근거의 분자/분모 충돌'})
                return result
    refs = first['refs'] + second['refs']
    result['evidence_fingerprint'] = fingerprint([{'id':e.get('execution_id') or e.get('id'),
        'data_version':e.get('data_version'),'conditions':e.get('conditions'),'result':e.get('result')}
        for e in sorted(executions,key=lambda e:str(e.get('execution_id') or e.get('id')))
        if 'execution:' + str(e.get('execution_id') or e.get('id')) in refs])
    def check(kind, excerpt, expected, actual=None, error=False, explanation='', scope='overall'):
        mismatch = error or actual is not None and abs(expected - actual) > TOLERANCE
        row = {'kind': kind, 'claim': excerpt.strip(), 'status': 'error' if mismatch else 'matched',
               'fact_id':fingerprint({'kind':kind,'scope':scope})[:24],
               'expected': str(expected), 'reported': str(actual) if actual is not None else None,
               'evidence_refs': refs, 'reason': explanation or f'실제 분자/분모로 계산한 값은 {expected:.4f}입니다.',
               'improvement': '선택한 두 주의 분자·분모와 계산 기준을 확인하고 해당 주장·결론을 수정하세요.'}
        result['checks'].append(row)
        if mismatch: result['errors'].append(row)
    text = report_text(report)
    for sentence in _sentences(text):
        if not _assertion(sentence):
            continue
        # Explicit week labels + fractions/rates; unscoped numbers are not guessed.
        for match in re.finditer(r'([12])\s*주차\s*(?:전체\s*)?(?:(?:튜토리얼\s*3\s*단계\s*)?완료율\s*(?:은|는|이|:|=)?\s*)?(?:(\d+)\s*/\s*(\d+)\s*=\s*)?([+-]?\d+(?:\.\d+)?)\s*%(?!\s*[pP])', sentence):
            index = int(match[1]) - 1
            # A channel immediately before the week is a scoped channel claim.
            channel_match = re.search(r'(ads|organic)\s*(?:의\s*)?$', sentence[:match.start()], re.I)
            counts = weeks[index]['counts'].get(channel_match[1].lower()) if channel_match else {'n': weeks[index]['n'], 'k': weeks[index]['k']}
            if not counts or not counts['n']: continue
            scope = f'{index + 1}:{channel_match[1].lower() if channel_match else "전체"}'
            check('completion_rate', match[0], 100 * counts['k'] / counts['n'], _number(match[4]), scope=scope)
            if match[2]:
                check('numerator', match[0], counts['k'], _number(match[2]), scope=scope)
                check('denominator', match[0], counts['n'], _number(match[3]), scope=scope)
        change = re.search(r'전체\s*완료율[^.!?\n]*?([+-]?\d+(?:\.\d+)?)\s*(%\s*p|퍼센트\s*포인트|%)\s*(?:만큼\s*)?(감소|하락|낮|줄|증가|상승|높)', sentence, re.I)
        if change:
            delta = second['rate'] - first['rate']
            if change[2].strip() == '%':
                if first['rate'] == 0: continue
                delta = 100 * delta / first['rate']
            reported = _number(change[1])
            if not change[1].startswith(('-', '+')):
                reported *= -1 if change[3] in ('감소', '하락', '낮', '줄') else 1
            check('relative_change' if change[2].strip() == '%' else 'percentage_point_change', change[0], delta, reported)
        composition = re.search(r'(?:유입\s*)?(?:채널\s*)?구성[^.!?\n]*?(?:만\s*바꾸|만으로|만\s*변경|변화만)[^.!?\n]*?(?:전체\s*\d+(?:\.\d+)?\s*%|전체\s*(?:완료율)?\s*하락|완전히\s*설명|전부\s*설명)', sentence)
        if composition and set(first['counts']) == set(second['counts']) and '전체' not in first['counts'] and all(c['n'] for c in first['counts'].values()):
            # Explicit alternative reference bases are left to semantic evaluation.
            if re.search(r'2\s*주차.*(?:고정|기준)|비교\s*주.*(?:고정|기준)', sentence): continue
            expected = sum((100 * first['counts'][name]['k'] / first['counts'][name]['n']) * counts['n'] / second['n'] for name, counts in second['counts'].items())
            reported_match = re.search(r'전체\s*(?:완료율\s*)?([+-]?\d+(?:\.\d+)?)\s*%', composition[0])
            check('composition_only', sentence, expected, _number(reported_match[1]) if reported_match else second['rate'],
                  explanation=f'1주차 채널 비율을 고정하고 2주차 구성만 적용하면 {expected:.4f}%입니다. 실제 2주차는 {second["rate"]:.4f}%입니다. 구성만으로 설명한다는 주장을 이 기준으로 확인하세요.')
    result['status'] = 'errors_found' if result['errors'] else 'checked_supported_claims' if result['checks'] else 'not_checked'
    if not result['checks']:
        result['notes'].append({'reason': '지원 형식의 명확한 확정 주장이 없음; 정답 판정 아님'})
    return result


def apply_verification(rows, verification, report_version):
    """Cap one criterion only; preserve null/system holds and provider grades."""
    if not verification['errors']: return
    row = next((r for r in rows if r['id'] == 'evidence_interpretation'), None)
    if row is None or row.get('grade') is None: return
    row['provider_grade'] = row['grade']
    row['grade'] = min(row['grade'], 1)
    row['verification_source'] = verification['version']
    row['provider_reason'] = row['reason']
    row['provider_improvement'] = row['improvement']
    row['provider_deductions'] = deepcopy(row.get('deductions', []))
    distinct = {e.get('fact_id') or fingerprint(e['claim'])[:24]:e for e in verification['errors']}
    row['deductions'] = [{'issue_id':'calculation:'+ident, 'kind':'arithmetic',
        'check_id':row['id']+':core_error','claim':error['claim'],
        'evidence_refs':[f'report:{report_version}']+error['evidence_refs'],
        'reason':error['reason'],'source':'deterministic_public_calculation'} for ident,error in distinct.items()]
    row['reason'] = '공개 실행 근거와 보고 주장의 계산 불일치: ' + ' / '.join(dict.fromkeys(e['reason'] for e in verification['errors']))
    row['improvement'] = verification['errors'][0]['improvement']
    row.pop('improvement_source', None)
    row['evidence_refs'] = list(dict.fromkeys(row['evidence_refs'] + [f'report:{report_version}'] + [ref for error in verification['errors'] for ref in error['evidence_refs']]))
