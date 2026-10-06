"""Declarative ratio/sum checks for future public task types, without SQL or eval.

Metric labels, query conditions and columns must be frozen in the public task.
Only explicit label/value/unit assertions are supported; this is not an NLP oracle.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
from .evaluation_quality import fingerprint

VERSION = 'declared-metrics-v1'


def verify_declared_metrics(task, report, executions, base):
    definition = task.get('arithmetic_contract')
    if definition is None: return base
    result = deepcopy(base)
    result['contract_status'] = 'invalid'
    result['scope'] += ' 공개 계약에 등록한 지표 라벨·값·단위의 합계/가중 비율 확정 주장도 확인합니다.'
    rules = definition.get('metrics') if isinstance(definition, dict) else None
    if not isinstance(rules, list) or not 1 <= len(rules) <= 20 or definition.get('version') != VERSION:
        result['notes'].append({'reason':'등록 지표의 검산 계약 형식을 확인하지 못했습니다.'}); return result
    ids, labels = set(), set()
    required_conditions = {'metric','start','end','timezone','unit','filters','group_by'}
    for rule in rules:
        if not isinstance(rule, dict) or any(not isinstance(rule.get(k), str) or not 1 <= len(rule[k]) <= 80 for k in ('id','label','unit')) or rule.get('operation') not in ('ratio','sum') or rule['id'] in ids or rule['label'] in labels or not isinstance(rule.get('conditions'), dict) or not required_conditions <= rule['conditions'].keys():
            result['notes'].append({'reason':'등록 지표에 중복/누락된 ID·라벨·단위·연산·비교 조건이 있습니다.'}); return result
        ids.add(rule['id']); labels.add(rule['label'])
        fields = ('numerator_column','denominator_column') if rule['operation']=='ratio' else ('value_column',)
        if any(not isinstance(rule.get(k),str) or not rule[k] for k in fields) or not task.get('data_version') or any(not isinstance(rule['conditions'][k],str) or not rule['conditions'][k] for k in ('metric','start','end','timezone','unit')) or not isinstance(rule['conditions']['filters'],dict) or rule['operation']=='ratio' and rule['unit']!='%':
            result['notes'].append({'reason':'등록 지표의 컬럼·데이터 버전·단위·기간 계약이 불완전합니다.'}); return result
    result['contract_status'] = 'valid'
    from .discord_verification import report_text, _assertion, _sentences
    selected = report.get('evidence_refs', [])
    selected = {str(v).removeprefix('execution:') for v in selected} if isinstance(selected, list) else set()
    used = []
    for rule in rules:
        values, refs = [], []
        for execution in executions:
            ident = str(execution.get('execution_id') or execution.get('id'))
            data = execution.get('result', {})
            if ident not in selected or execution.get('conditions') != rule['conditions'] or execution.get('data_version') != task.get('data_version'): continue
            if execution.get('status') not in ('success','succeeded') or data.get('status') not in ('success','succeeded') or data.get('result_complete') is not True or data.get('truncated'): continue
            try:
                columns = [c['name'] if isinstance(c,dict) else c for c in data['columns']]
                rows = [dict(zip(columns,row)) for row in data['rows'] if len(row)==len(columns)]
                if not rows or len(rows)!=len(data['rows']) or data.get('total_row_count',len(rows))!=len(rows): raise ValueError()
                group_column = rule.get('group_column')
                if rule['conditions']['group_by']:
                    if not group_column or len({r[group_column] for r in rows})!=len(rows): raise ValueError()
                elif len(rows)!=1: raise ValueError()
                if 'group_value' in rule:
                    rows = [r for r in rows if r[group_column]==rule['group_value']]
                    if len(rows)!=1: raise ValueError()
                def number(value):
                    if isinstance(value,bool): raise ValueError()
                    value=Decimal(str(value))
                    if not value.is_finite(): raise ValueError()
                    return value
                if rule['operation']=='sum':
                    value=sum(number(r[rule['value_column']]) for r in rows)
                else:
                    pairs=[(number(r[rule['numerator_column']]),number(r[rule['denominator_column']])) for r in rows]
                    if any(n<0 or k<0 or k>n or k!=k.to_integral_value() or n!=n.to_integral_value() for k,n in pairs): raise ValueError()
                    denominator=sum(n for _,n in pairs)
                    if not denominator: raise ValueError()
                    value=100*sum(k for k,_ in pairs)/denominator
                    if rule['unit']!='%': raise ValueError()
                values.append(value); refs.append('execution:'+ident); used.append(execution)
            except (KeyError,ValueError,TypeError,InvalidOperation,ArithmeticError):
                result['notes'].append({'execution_id':ident,'reason':'등록 지표의 전체 행·단위·분자/분모를 확인하지 못했습니다.'})
        if not values or len(set(values))!=1:
            result['notes'].append({'reason':rule['label']+': 동일 공개 조건의 완전한 근거 없음 또는 반복 조회 충돌'}); continue
        pattern = re.compile(r'(?<![\w가-힣])'+re.escape(rule['label'])+r'\s*(?:은|는|:|=)?\s*([+-]?\d+(?:\.\d+)?)\s*'+re.escape(rule['unit'])+(r'(?!\s*[pP])' if rule['unit']=='%' else ''))
        for sentence in _sentences(report_text(report)):
            if not _assertion(sentence): continue
            for match in pattern.finditer(sentence):
                expected, actual = values[0], Decimal(match[1])
                tolerance=Decimal('0.06') if rule['operation']=='ratio' else Decimal('0.005')
                error=abs(expected-actual)>tolerance
                row={'kind':'declared_'+rule['operation'],'fact_id':fingerprint({'metric':rule['id'],'operation':rule['operation']})[:24],
                     'claim':match[0],'status':'error' if error else 'matched','expected':str(expected),'reported':str(actual),
                     'evidence_refs':refs,'reason':f'공개 계약 {rule["id"]}의 {rule["operation"]} 계산 결과는 {expected:.4f}{rule["unit"]}입니다.',
                     'improvement':'공개 지표의 조건·단위·분자·분모/금액과 저장 실행 근거를 확인하고 주장을 수정하세요.'}
                result['checks'].append(row)
                if error: result['errors'].append(row)
    result['version'] = base['version']+'+'+VERSION
    result['evidence_fingerprint'] = fingerprint({'base':base.get('evidence_fingerprint'),
        'contract':definition,'used':sorted(used,key=lambda e:str(e.get('execution_id') or e.get('id')))})
    result['status']='errors_found' if result['errors'] else 'checked_supported_claims' if result['checks'] else 'not_checked'
    return result
