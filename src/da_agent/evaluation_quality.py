"""Public, provider-independent evaluation contracts and conservative gates.

The gate validates provenance and consistency, not the truth of arbitrary prose.
Held responses stay held; it never repairs a score on its own.
"""
from copy import deepcopy
import hashlib
import json
import re

VERSION = 'evaluation-quality-v1'
KINDS = ('missing_required', 'core_error', 'arithmetic', 'causal_claim', 'unsupported_claim')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    default=str).encode()).hexdigest()


def contract(task):
    criteria = task['rubric']['criteria']
    checks = {f"{c['id']}:{kind}": {'criterion_id': c['id'], 'kind': kind,
              'description': c[field]} for c in criteria
              for kind, field in (('required', 'required'), ('core_error', 'core_error'))}
    owners = dict(task['rubric'].get('error_owners', {}))
    if 'evidence_interpretation' in {c['id'] for c in criteria}:
        owners.setdefault('arithmetic', 'evidence_interpretation')
        owners.setdefault('causal_claim', 'evidence_interpretation')
    public = {'version': VERSION, 'checks': checks, 'error_owners': owners,
              'deduction_kinds': list(KINDS), 'max_repair_attempts': 1,
              'failure_policy': 'hold_score', 'rubric': deepcopy(task['rubric'])}
    public['fingerprint'] = fingerprint(public)
    return public


def normalized(value):
    return re.sub(r'\s+|[.,;:!。]', '', value).casefold()


def validate_deductions(rows, public_contract, report_text, refs, verification):
    """Validate claimed deductions and return repairable provider issues."""
    issues, seen_ids, seen_facts = [], {}, {}
    checks, owners = public_contract['checks'], public_contract['error_owners']
    body = normalized(report_text)

    def issue(code, criterion, detail):
        record = {'code': code, 'criterion_id': criterion, 'detail': detail}
        if record not in issues: issues.append(record)

    for row in rows:
        cid, grade = row['id'], row.get('grade')
        deductions = row.get('deductions', [])
        if not isinstance(deductions, list) or len(deductions) > 10:
            issue('deduction_contract', cid, '감점 목록은 최대10개 배열이어야 합니다.')
            continue
        if grade is not None and grade < 3 and not deductions:
            issue('missing_deduction', cid, '등급0~2에는 공개 기준에 연결한 감점 근거가 필요합니다.')
        if grade is None: continue
        if grade >= 3 and deductions:
            issue('grade_deduction_conflict', cid, '필수 충족 등급에 미해결 핵심 감점을 동시에 기록할 수 없습니다.')
        for deduction in deductions:
            if not isinstance(deduction, dict):
                issue('deduction_contract', cid, '감점 근거는 객체여야 합니다.'); continue
            ident, kind, check = (deduction.get(k) for k in ('issue_id', 'kind', 'check_id'))
            quote, evidence = deduction.get('claim'), deduction.get('evidence_refs')
            if not isinstance(ident, str) or not ident.strip() or len(ident) > 120 or kind not in KINDS:
                issue('deduction_contract', cid, '오류 ID와 허용 종류를 확인하세요.'); continue
            if not isinstance(check, str) or check not in checks or checks[check]['criterion_id'] != cid:
                issue('deduction_check_mismatch', cid, '이 항목의 공개 required/core_error 기준을 정확히 연결하세요.')
            elif (kind == 'missing_required') != (checks[check]['kind'] == 'required'):
                issue('deduction_check_mismatch', cid, '필수 조건 누락과 확인된 핵심 오류의 공개 기준을 구분하세요.')
            if not isinstance(quote, str) or not isinstance(deduction.get('reason'), str) or not deduction['reason'].strip():
                issue('deduction_contract', cid, '실제 인용과 구체적인 감점 이유가 필요합니다.'); continue
            if not isinstance(evidence, list) or not evidence or any(not isinstance(r, str) or r not in refs for r in evidence):
                issue('deduction_unknown_ref', cid, '감점 근거 ID가 없거나 공개 근거에 존재하지 않습니다.'); continue
            if any(r.startswith('execution:') and refs[r].get('status') not in ('success', 'succeeded') for r in evidence):
                issue('deduction_failed_execution', cid, '실패 실행으로 학습자 오류를 확정할 수 없습니다.')
            if any(r not in row['evidence_refs'] for r in evidence):
                issue('deduction_ref_mismatch', cid, '감점 인용은 항목 인용에도 포함되어야 합니다.')
            if quote.strip():
                if normalized(quote) not in body:
                    issue('deduction_quote_missing', cid, '인용 문장이 현재 보고/후속 답변에 없습니다.')
            elif kind != 'missing_required':
                issue('deduction_quote_missing', cid, '누락 이외의 오류에는 실제 보고 문장이 필요합니다.')
            if kind in owners and owners[kind] != cid:
                issue('duplicate_error_owner', cid, f'{kind} 결함의 담당 항목은 {owners[kind]}입니다.')
            if kind == 'arithmetic' and not any(normalized(quote) in normalized(e['claim']) or
                    normalized(e['claim']) in normalized(quote) for e in verification.get('errors', []) if quote.strip()):
                issue('arithmetic_unverified', cid, '확인된 검산 오류에 연결하지 못한 산술 감점입니다.')
            if kind == 'causal_claim' and re.search(r'않|못|없|보류|기각|미확인|\?', quote):
                issue('causal_claim_ambiguous', cid, '부정·보류·기각·질문을 인과 확정 오류로 단정할 수 없습니다.')
            # Independent missing requirements can share an empty quote.
            fact = (kind, normalized(quote)) if quote.strip() else (kind, check)
            for cache, key in ((seen_ids, ident), (seen_facts, fact)):
                if key in cache:
                    old_cid = cache[key]
                    issue('duplicate_deduction', cid, '같은 결함을 중복 배정했습니다. 하나의 담당 항목에만 연결하세요.')
                    issue('duplicate_deduction', old_cid, '같은 결함을 중복 배정했습니다. 하나의 담당 항목에만 연결하세요.')
                else:
                    cache[key] = cid
        # Catch known composition arithmetic even if the provider hides it in
        # a narrative reason or labels its deduction as another kind.
        if grade < 3 and cid != owners.get('arithmetic'):
            reason = str(row.get('reason', ''))
            if any(e['kind'] == 'composition_only' for e in verification.get('errors', [])) and re.search(r'구성|비중|믹스', reason) and re.search(r'오류|잘못|틀|계산|80\s*%|70\s*%', reason):
                issue('possible_arithmetic_duplicate', cid, '구성 계산 오류가 다른 항목의 감점 이유에 반복된 것으로 의심됩니다. 독립된 결함 여부를 재검토하세요.')
    return issues


def revision_feedback(previous, current):
    """Same-policy, same-evidence error changes, without inventing learning."""
    old, new = (r.get('arithmetic_verification') for r in (previous, current))
    feedback = {'status': 'not_comparable', 'resolved': [], 'remaining': [], 'new': [],
                'unverified': [], 'note': '보고 주장 수정 관측이며 학습 효과 입증은 아닙니다.'}
    if not old or not new or old.get('version') != new.get('version'):
        feedback['unverified'].append('이전 검산 기록이 없거나 검산 정책이 다릅니다.'); return feedback
    if old.get('evidence_fingerprint') != new.get('evidence_fingerprint') or not new.get('evidence_fingerprint'):
        feedback['unverified'].append('선택 근거·조건·데이터 버전이 달라 동일 오류 해결을 확정할 수 없습니다.'); return feedback
    if new.get('status') == 'not_checked':
        feedback['unverified'].append('수정 보고의 지원 형식 주장을 확인하지 못했습니다.'); return feedback
    matched = {c.get('fact_id') for c in new['checks'] if c['status'] == 'matched'}
    current_errors = {e.get('fact_id'): e for e in new['errors']}
    old_errors = {e.get('fact_id'): e for e in old['errors']}
    for key, error in old_errors.items():
        if key in current_errors:
            feedback['remaining'].append(current_errors[key])
        elif key in matched:
            feedback['resolved'].append(error)
        else:
            feedback['unverified'].append('이전 오류 주장 삭제·표현 변경으로 재검산 못함: ' + error['claim'])
    feedback['new'] = [e for key, e in current_errors.items() if key not in old_errors]
    feedback['status'] = 'compared'
    return feedback


def summarize_suite(cases, repeats, contract_fingerprint, *, max_score_range=5):
    """No completion, stability or semantic pass is inferred from missing runs."""
    failures, summaries = [], []
    required = {'correct', 'numeric_error', 'valid_alternative', 'uncertainty', 'missing_evidence', 'system_failure'}
    if len(cases) != len(required) or {c['id'] for c in cases} != required or repeats < 3:
        failures.append('고정 표본6종 누락 또는 중복/추가')
    for case in cases:
        results = case.get('results', [])
        scores = [r['total'] for r in results if not r.get('held') and r.get('total') is not None]
        problems = []
        if len(results) != repeats: problems.append('반복 수 미충족')
        for result in results:
            if case['id'] == 'system_failure':
                if not result.get('held') or result.get('total') is not None:
                    problems.append('시스템 실패의 점수 확정')
                continue
            if result.get('held'): problems.append('평가 보류'); continue
            if result.get('quality_validation', {}).get('status') != 'passed': problems.append('신뢰성 계약 미통과')
            if result.get('quality_validation', {}).get('contract_fingerprint') != contract_fingerprint:
                problems.append('평가 계약 지문 불일치')
            grades = {r['id']: r.get('grade') for r in result.get('criteria', [])}
            for cid, bounds in case.get('expected_grades', {}).items():
                if type(grades.get(cid)) is not int or not bounds[0] <= grades[cid] <= bounds[1]:
                    problems.append('기대 등급 불일치: ' + cid)
            if case['id'] == 'numeric_error' and not result.get('arithmetic_verification', {}).get('errors'):
                problems.append('계산 오류 미검출')
            if case['id'] == 'numeric_error' and (type(grades.get('evidence_interpretation')) is not int or grades['evidence_interpretation']>1):
                problems.append('확인된 계산 오류의 등급 상한 미반영')
            if case['id'] in ('correct', 'valid_alternative', 'uncertainty') and result.get('arithmetic_verification', {}).get('errors'):
                problems.append('타당한 보고의 허위 계산 오류')
        spread = round(max(scores) - min(scores), 2) if len(scores) >= 2 else None
        if case['id'] != 'system_failure' and (spread is None or spread > max_score_range):
            problems.append('점수 편차 초과 또는 관측 부족')
        criterion_ranges = {}
        for cid in case.get('expected_grades', {}):
            values = [row['grade'] for result in results if not result.get('held')
                      for row in result.get('criteria', []) if row['id']==cid and type(row.get('grade')) is int]
            criterion_ranges[cid] = max(values)-min(values) if len(values)>=2 else None
        summaries.append({'id': case['id'], 'runs': len(results), 'scores': scores,
                          'criterion_ranges':criterion_ranges,
                          'score_range': spread, 'failures': sorted(set(problems))})
        failures.extend(case['id'] + ': ' + p for p in sorted(set(problems)))
    return {'version': VERSION, 'contract_fingerprint': contract_fingerprint,
            'repeats': repeats, 'max_score_range': max_score_range, 'cases': summaries,
            'verdict': 'fail' if failures else 'pass', 'failures': failures,
            'human_review': 'pending', 'scope': '고정 표본 자동 검사이며 모든 새 주제의 의미 품질 승인이 아님'}


def quality_gate(fixtures, run, *, model, code_hashes):
    """A certificate cannot be reused after input, model or code changes.

Returns eligibility for the registered automatic-test scope only. A generator
can call this before offering a new type, and must keep held drafts unpublished.
"""
    reasons=[]
    if run.get('status')!='completed': reasons.append('반복 검사 미완료')
    if run.get('fixture_fingerprint')!=fingerprint(fixtures): reasons.append('고정 표본/문제 정의 지문 불일치')
    if run.get('model')!=model: reasons.append('평가 모델 변경')
    if not code_hashes or run.get('code_hashes')!=code_hashes: reasons.append('평가 코드 변경 또는 버전 근거 없음')
    expected=contract(fixtures['task'])['fingerprint']
    expected_cases={c['id']:c for c in fixtures.get('cases',[])}
    weights={c['id']:c['weight'] for c in fixtures['task']['rubric']['criteria']}
    for case in run.get('cases',[]):
        frozen=expected_cases.get(case.get('id'))
        if not frozen or any(case.get(key)!=frozen.get(key) for key in ('report','executions','expected_grades')):
            reasons.append('검사 중 표본·기대 판정 변경')
        for result in case.get('results',[]):
            if result.get('held'): continue
            grades={r.get('id'):r.get('grade') for r in result.get('criteria',[])}
            if set(grades)!=set(weights) or len(result.get('criteria',[]))!=len(weights) or any(type(g) is not int or g not in range(5) for g in grades.values()):
                reasons.append('평가 항목·등급 누락 또는 중복'); continue
            total=round(sum(weights[k]*grades[k]/4 for k in weights),2)
            if result.get('total')!=total: reasons.append('저장 총점과 항목별 재계산 불일치')
            if frozen:
                from .discord_verification import verify_report, report_text
                from .evaluation_metrics import verify_declared_metrics
                task, report, executions=fixtures['task'],frozen['report'],frozen.get('executions',[])
                proof=verify_declared_metrics(task,report,executions,verify_report(task,report,executions))
                if fingerprint(result.get('arithmetic_verification'))!=fingerprint(proof):
                    reasons.append('검산 기록과 공개 실행의 독립 재계산 불일치')
                refs={f"report:{report.get('version',1)}":report}
                refs.update({'execution:'+str(e.get('execution_id') or e.get('id')):e for e in executions})
                if validate_deductions(result['criteria'],contract(task),report_text(report),refs,proof):
                    reasons.append('저장된 감점 근거 계약 재검증 실패')
    summary=summarize_suite(run.get('cases',[]),run.get('summary',{}).get('repeats',0),expected)
    # Recompute from individual runs; do not trust a caller's saved "pass" flag.
    if summary['verdict']!='pass': reasons.extend(summary['failures'])
    return {'status':'held' if reasons else 'eligible','reasons':reasons,
            'policy':VERSION,'scope':'등록한 고정 표본의 자동 품질 확인',
            'human_review':'pending','retry':'문제·지표 정의와 실패 항목을 수정한 뒤 같은 표본으로 다시 검사하세요.'}
