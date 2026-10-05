"""Fixed evaluation contracts and deterministic saved-result verification."""
import copy
import hashlib
import json
import math
import re
from decimal import Decimal, ROUND_HALF_UP
from .reviews import normalize_review

RULES_VERSION = 'evaluation-v2'


def freeze_evaluation(public, reference):
    weights = copy.deepcopy(reference.get('weights') or public.get('weights'))
    if not isinstance(weights, dict) or not weights or any(type(w) not in (int, float) or not math.isfinite(w) or w <= 0 for w in weights.values()) or sum(weights.values()) != 100:
        raise ValueError('positive evaluation weights must sum to 100')
    if public.get('weights') and public['weights'] != weights:
        raise ValueError('public and private weights differ')
    if public.get('task_kind') == 'design' and 'sql_accuracy' in weights:
        raise ValueError('design task cannot require SQL accuracy')
    frozen = {k: copy.deepcopy(reference[k]) for k in ('expected', 'comparison_expected', 'rubric', 'required_judgments', 'required_evidence', 'allowed_limitations', 'excluded_criteria', 'verification_contracts') if k in reference}
    frozen.update(weights=weights, completion_conditions=copy.deepcopy(public.get('completion_conditions', [])), rules_version=RULES_VERSION)
    frozen['contract_hash'] = hashlib.sha256(json.dumps(frozen, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return frozen


def verify_evidence(evidence, expected, definition=None):
    """Compare a full executed aggregate, never SQL strings or report assertions.

    A caller may pass an explicit matching definition/expected contract for an
    allowed alternative. Unsupported output shapes remain unverified.
    """
    eid = evidence.get('saved_execution_id')
    result = evidence.get('result', {})
    base = {'saved_execution_id': eid, 'status': 'unverified', 'checks': [], 'reason': '저장된 완전한 실행 결과로 계산을 확인할 수 없습니다.'}
    if not eid or result.get('status') != 'success' or result.get('result_complete') is not True or result.get('truncated'):
        return base
    if definition and evidence.get('definition') != definition:
        base['reason'] = '대안 정의에 맞는 고정 검증 계약이 필요합니다.'
        return base
    rows = result.get('rows', [])
    names = [c.get('name') for c in result.get('columns', [])]
    if len(rows) != 1 or len(names) != len(set(names)) or not expected or not set(expected).issubset(names):
        base['reason'] = '전체 집계 단위·필수 열을 확인할 수 없습니다.'
        return base
    row = rows[0]
    if isinstance(row, dict):
        actual = row
    elif isinstance(row, list) and len(row) == len(names):
        actual = dict(zip(names, row))
    else:
        return base
    for key, wanted in expected.items():
        value = actual.get(key)
        # SqlRunner encodes PostgreSQL NUMERIC as decimal strings to preserve precision.
        if isinstance(value, str) and type(wanted) in (int, float, Decimal) and re.fullmatch(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', value):
            value = Decimal(value)
        if wanted is None:
            matches = value is None
        elif type(value) in (int, float, Decimal) and type(wanted) in (int, float, Decimal) and math.isfinite(float(value)):
            matches = abs(float(value) - float(wanted)) <= 1e-8
        else:
            matches = False
        base['checks'].append({'field': key, 'matches': matches})
    base.update(status='verified' if all(c['matches'] for c in base['checks']) else 'mismatch', reason='실제 저장 결과를 고정 집계 기준과 비교했습니다.')
    return base


def exposure_detected(feedback, private, known):
    """Defense-in-depth lexical guard. Semantic leak quality needs human review."""
    output = json.dumps(feedback, ensure_ascii=False)
    visible = json.dumps(known, ensure_ascii=False)
    numeric_sources = []
    def executed_numbers(value):
        if isinstance(value, dict):
            if value.get('status') == 'success' and isinstance(value.get('rows'), list):
                numeric_sources.append(value['rows'])
            for nested in value.values():
                executed_numbers(nested)
        elif isinstance(value, list):
            for nested in value:
                executed_numbers(nested)
    executed_numbers(known)
    for source in known.get('sources', []) if isinstance(known, dict) else []:
        if source.get('state') == 'public':
            numeric_sources.append(source.get('content'))
    numeric_visible = json.dumps(numeric_sources, ensure_ascii=False)
    def compact(text):
        return re.sub(r'\s+', ' ', text).strip().casefold()
    for key in ('sql', 'reference_sql', 'cause', 'actual_cause', 'event_name', 'variation_id', 'variant_id', 'seed'):
        value = private.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            phrase = compact(str(value))
            if phrase in compact(output) and phrase not in compact(visible):
                return True
    # Unknown private numeric expectations must not be presented as observed.
    for value in (private.get('expected') or {}).values():
        if type(value) in (int, float) and abs(value) >= 10:
            for number in {str(value), str(round(value, 1))}:
                pattern = r'(?<![\d.])' + re.escape(number) + r'(?![\d.])'
                if re.search(pattern, output) and not re.search(pattern, numeric_visible):
                    return True
    return False


def verify_comparison(evidence, comparison_expected):
    """Verify full group/period rows against a frozen independent reference.

    Contract is {columns: [name, ...], rows: [[value, ...], ...]}. Row order
    and SQL spelling have no significance; complete group membership does.
    """
    base = {'saved_execution_id': evidence.get('saved_execution_id'), 'status': 'unverified', 'checks': [], 'reason': '완전한 비교 결과와 고정 비교 기준이 필요합니다.'}
    result = evidence.get('result', {})
    if not base['saved_execution_id'] or result.get('status') != 'success' or result.get('result_complete') is not True or result.get('truncated'):
        return base
    if not isinstance(comparison_expected, dict):
        return base
    columns, expected = comparison_expected.get('columns'), comparison_expected.get('rows')
    actual_columns = [c.get('name') for c in result.get('columns', [])]
    rows = result.get('rows')
    if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)) or not isinstance(expected, list) or len(expected) > 1000 or not isinstance(rows, list) or len(rows) > 1000 or len(actual_columns) != len(set(actual_columns)) or not set(columns).issubset(actual_columns):
        return base
    if any(not isinstance(row, list) or len(row) != len(columns) for row in expected):
        return base
    projected = []
    for row in rows:
        if isinstance(row, list) and len(row) == len(actual_columns):
            mapping = dict(zip(actual_columns, row))
        elif isinstance(row, dict) and set(columns).issubset(row):
            mapping = row
        else:
            return base
        projected.append([mapping[c] for c in columns])
    def equal(left, right):
        for a, b in zip(left, right):
            if type(a) in (int, float, Decimal) and type(b) in (int, float, Decimal):
                if not math.isfinite(float(a)) or not math.isfinite(float(b)) or abs(float(a)-float(b)) > 1e-8:
                    return False
            elif type(a) is not type(b) or a != b:
                return False
        return True
    remaining = list(expected)
    matched = len(projected) == len(expected)
    for row in projected:
        index = next((i for i, want in enumerate(remaining) if equal(row, want)), None)
        if index is None:
            matched = False
        else:
            remaining.pop(index)
    matched = matched and not remaining
    base.update(status='verified' if matched else 'mismatch', checks=[{'field': 'comparison_rows', 'matches': matched}], reason='전체 비교 집단·기간과 집계를 고정 비교 기준으로 확인했습니다.')
    return base


def verify_for_contract(evidence, frozen):
    definition = evidence.get('definition')
    if definition:
        contract = (frozen.get('verification_contracts') or {}).get(definition)
        if not contract:
            return {'saved_execution_id': evidence.get('saved_execution_id'), 'status': 'unverified', 'checks': [], 'reason': '대안 정의를 검증할 고정 계약이 없습니다. 필요한 관측 조건을 확인하세요.'}
    else:
        contract = frozen
    if contract.get('comparison_expected') is not None:
        return verify_comparison(evidence, contract['comparison_expected'])
    return verify_evidence(evidence, contract.get('expected'), definition)


def normalize_evaluation(result, report, evidence, frozen, private=None, explanation_viewed=False):
    """Strict v2 wrapper; legacy normalize_review remains available unchanged."""
    failed = dict(status='failed', feedback=None, model=result.get('model'), error={'code': 'review_format', 'message': '고정 평가 조건·동일 제출본 근거를 확인하지 못했습니다.'})
    try:
        hash_input = {k: v for k, v in frozen.items() if k != 'contract_hash'}
        if frozen.get('contract_hash') != hashlib.sha256(json.dumps(hash_input, ensure_ascii=False, sort_keys=True).encode()).hexdigest():
            return failed
        linked = {r['saved_execution_id'] for c in report['claims'] for r in c.get('evidence_refs', [])}
        available = {e['saved_execution_id'] for e in evidence}
        if len(available) != len(evidence) or not available.issubset(linked):
            return failed
        for item in evidence:
            if report.get('attempt_id') and item.get('attempt_id') != report['attempt_id']:
                return failed
            for key in ('dataset_id', 'problem_id', 'release_version'):
                if key in report and item.get(key) != report[key]:
                    return failed
        raw = result.get('feedback', result.get('text', result.get('output_text')))
        if isinstance(raw, str):
            raw = re.sub(r'^```(?:json)?\s*([\s\S]*?)\s*```$', r'\1', raw.strip())
        value = json.loads(raw) if isinstance(raw, str) else copy.deepcopy(raw)
        if result.get('status') in {'completed', 'success'} or result.get('state') == 'completed':
            if not isinstance(value, dict) or set(value) - {'criteria', 'strengths', 'improvements', 'next_steps', 'uncertainty'}:
                return failed
            if not isinstance(value.get('uncertainty'), str) or len(value['uncertainty']) > 4000:
                return failed
            for item in value['criteria']:
                if set(item) != {'key', 'level', 'reason', 'claim_ids', 'saved_execution_ids'} or not isinstance(item['reason'], str) or len(item['reason']) > 4000:
                    return failed
                for key in ('claim_ids', 'saved_execution_ids'):
                    if not isinstance(item[key], list) or any(not isinstance(x, str) for x in item[key]) or len(item[key]) != len(set(item[key])):
                        return failed
            result = dict(result, feedback=json.dumps(value, ensure_ascii=False))
        normalized = normalize_review(result, report, evidence, frozen['weights'])
        if normalized['status'] != 'completed':
            return normalized
        feedback = normalized['feedback']
        feedback['total_score'] = float(sum(Decimal(str(c['weight'])) * c['level'] / 4 for c in feedback['criteria']).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))
        checks = [verify_for_contract(item, frozen) for item in evidence]
        # A provider cannot confirm a numeric calculation contradicted by DB output.
        accuracy = next((c for c in feedback['criteria'] if c['key'] == 'sql_accuracy'), None)
        if accuracy and accuracy['level'] >= 3:
            referenced = {c['saved_execution_id']: c for c in checks if c['saved_execution_id'] in accuracy['saved_execution_ids']}
            if not referenced or any(c['status'] != 'verified' for c in referenced.values()):
                return dict(failed, error={'code': 'unverified_calculation', 'message': '완전한 저장 결과로 계산 정확성 판정을 확인하지 못했습니다.'})
        if not explanation_viewed and exposure_detected(feedback, private or frozen, {'report': report, 'evidence': evidence}):
            return dict(failed, error={'code': 'answer_exposure', 'message': '해설 전 비공개 정보가 포함된 리뷰를 차단했습니다. 제출본은 보존했습니다.'})
        normalized.update(rules_version=RULES_VERSION, contract_hash=frozen['contract_hash'], calculation_verification=checks)
        return normalized
    except (ValueError, TypeError, KeyError, AttributeError):
        return failed
