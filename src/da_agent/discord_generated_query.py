"""Natural-language queries validated against the current public data schema."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from .adaptive_tasks import Metric
from .analytical_metrics import compile_metric
from .errors import DomainError
from .sql_runner import check_query


def compile_public_select(query, task):
    """Resolve every table and column before the read-only runner can execute."""
    if not isinstance(query, str) or not query.strip() or len(query) > 20000:
        raise ValueError('조회 SQL 형식을 확인하세요.')
    schema = task['schema']
    tree = check_query(query, schema)
    if any(node.args.get('recursive') for node in tree.find_all(exp.With)):
        raise ValueError('재귀 조회는 지원하지 않습니다.')
    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    tables = list(dict.fromkeys(t.name for t in tree.find_all(exp.Table) if t.name not in ctes))
    if not tables:
        raise ValueError('현재 공개 표를 조회해야 합니다.')
    # SQLGlot resolves CTEs, aliases, stars, ambiguity and unknown columns using
    # only this session's public schema. The runner repeats the SQL policy check.
    qualified = qualify(tree, dialect='postgres', schema=schema,
                        infer_schema=False, validate_qualify_columns=True)
    compiled = qualified.sql(dialect='postgres')
    check_query(compiled, schema)
    return compiled, tables


def unavailable_message(parsed, task):
    missing = parsed.get('missing_data')
    if not isinstance(missing, list) or not 1 <= len(missing) <= 6 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 200 for item in missing):
        raise ValueError('부족한 자료를 명시해야 합니다.')
    available = '; '.join(f"{table}: {', '.join(columns)}" for table, columns in task['schema'].items())
    return ('현재 자료에는 요청을 확인할 다음 정보가 없습니다: ' + ', '.join(missing)
            + '.\n현재 조회 가능한 표·컬럼: ' + available
            + '.\n해당 정보를 포함한 자료가 필요합니다. 현재 있는 자료의 원본 행이나 조건별 비교·집계는 조회할 수 있습니다.')


def tables_for(task):
    types={'bigint':'integer','integer':'integer','double precision':'number','text':'category','timestamptz':'timestamp'}
    relations={(r['table'],r['column']):r for r in task['relationships']}
    tables={}
    for name,columns in task['schema'].items():
        ordered=list(columns)
        # The shared metric compiler joins only to the declared primary ID.
        target_ids=[r['target_column'] for r in task['relationships'] if r['target_table']==name]
        if target_ids:
            ordered=[target_ids[0]]+[c for c in ordered if c!=target_ids[0]]
        values=[]
        for c in ordered:
            fk=relations.get((name,c))
            values.append(NS(name=c,generator=NS(kind='foreign_key' if fk else types[columns[c]],
                table=fk['target_table'] if fk else None)))
        tables[name]=NS(columns=values)
    return tables


def compile_conditions(conditions,task):
    if not isinstance(conditions,dict): raise ValueError('조회 조건이 없습니다.')
    if conditions.get('operation') == 'select':
        if set(conditions) != {'operation', 'sql'}:
            raise ValueError('조회 SQL 조건을 확인하세요.')
        return compile_public_select(conditions['sql'], task)
    allowed={'table','operation','column','conditions','joins','group_by','denominator_conditions'}
    if set(conditions)-allowed: raise ValueError('지원하지 않는 조회 조건입니다.')
    metric=Metric.model_validate(dict(conditions,name='value',purpose='analysis'))
    return compile_metric(metric,tables_for(task)),[metric.table]+[j.table for j in metric.joins]


class GeneratedQueryEngine:
    def __init__(self,provider,runner,settings):
        self.provider,self.runner,self.settings=provider,runner,settings

    def resolve(self,text,public_task,previous_conditions=None,clarification=None,difficulty='intermediate',pending_query=None):
        public={k:public_task[k] for k in ('objective','schema','dictionary','relationships','timezone','period','metrics')}
        example={'state':'ready','conditions':{'table':next(iter(public_task['schema'])),'operation':'count','conditions':[],'joins':[],'group_by':[]}}
        messages=[{'role':'system','content':
            '사용자의 자연어 요청을 현재 public_task의 공개 데이터로 조회하는 PostgreSQL SELECT로 해석하고 JSON만 반환하세요. 결과 수치 생성 금지. '
            '원본 전체 행, 특정 컬럼, 필터, 정렬, 여러 집계, 산술 비교, 공개 표 연결, CTE를 지원합니다. '
            'ready에는 conditions={operation:"select",sql:"SELECT ..."}를 반환하세요. '
            '공개 schema의 표·컬럼만 사용하며 스키마 지정, 데이터 변경, 시스템 표, 외부 접근, 재귀 조회는 금지합니다. '
            '전체 데이터 요청은 집계로 바꾸지 말고 모든 공개 컬럼과 행을 조회하세요. 요청하지 않은 LIMIT이나 필터를 추가하지 마세요. '
            '안정적인 원본 행 순서는 공개 ID를 사용하세요. 표 연결은 공개 relationships를 확인하고 중복·누락으로 분석 단위가 바뀌지 않게 하세요. '
            '요청에 필요한 자료가 없으면 error/reason=missing_data와 missing_data 배열에 빠진 정보(예: 판매 아이템 목록, 가격, 변경 이력)를 구체적으로 적으세요. '
            '없는 자료를 만들어내거나 다른 지표로 대체하지 마세요. 필요 컬럼이 있으면 missing_data로 거절하지 마세요. '
            '지원하지 않는 계산은 error/reason=unsupported_query와 limitation에 필요한 계산을 적으세요. '
            '기존 집계 조건도 사용 가능: table,operation(count/distinct/sum/avg/min/max/ratio),column,conditions,joins,group_by,denominator_conditions. '
            '조건 항목 {column,operator(eq/gt/gte/lt/lte),value}. column은 table.column 또는 기본 표의 column. '
            'joins 항목 {table,source_column}은 공개 FK의 many-to-one 연결만 허용. group_by 최대3개. '
            'ratio는 해당 행 중 conditions 분자 / denominator_conditions로 제한한 전체 행 비율(0~1). '
            '기간 필터는 공개 시각 컬럼과 사용자 지정 경계를 사용하고 시간대를 포함한 ISO 문자열로 표현. '
            '모호한 기간·분모·단위는 clarification과 중립 질문으로 확인. 임의 기본 추론 금지. '
            'pending_query는 이전 미확정 의도이며 새 요청이 다른 조회이면 replaces_pending=true. '
            '모호하지 않은 전체 표 조회에는 기간·분모를 묻지 마세요. clarification에는 question, ready에는 conditions 전체. 예시: '+json.dumps(example)},
            {'role':'user','content':json.dumps({'request':text,'public_task':public,'previous_conditions':previous_conditions,
                'clarification':clarification,'pending_query':pending_query},ensure_ascii=False)}]
        try:
            result=self.provider.review(messages)
            meta={k:result[k] for k in ('usage','model') if k in result}
            if result.get('state')!='completed': return dict(state='error',reason=result.get('reason','provider_unavailable'),**meta)
            parsed=json.loads(result['text'])
            if not isinstance(parsed, dict):
                raise ValueError('조회 응답 형식 오류')
            if parsed.get('state')=='clarification' and isinstance(parsed.get('question'),str) and parsed['question'].strip():
                return dict(state='clarification',question=parsed['question'][:600],proposed_conditions={},help_type='request_confirmation',**meta)
            if parsed.get('state')!='ready':
                if parsed.get('state') == 'error' and parsed.get('reason') == 'missing_data':
                    return dict(state='error', reason='missing_data', missing_data=parsed['missing_data'],
                                message=unavailable_message(parsed, public_task), **meta)
                limitation = parsed.get('limitation')
                detail = limitation[:400] if isinstance(limitation, str) and limitation.strip() else '요청한 계산 방식'
                return dict(state='error',reason='unsupported_query',message=f'현재 조회에서 지원하지 않는 계산입니다: {detail}. 원본 행·조건 조회·정렬·집계 또는 공개 표의 비교를 요청할 수 있습니다.',**meta)
            conditions=parsed['conditions']
            sql,tables=compile_conditions(conditions,public_task)
            return dict(state='ready',conditions=conditions,sql=sql,replaces_pending=parsed.get('replaces_pending') is True,**meta)
        except (ValueError,KeyError,TypeError,AttributeError,DomainError,sqlglot.errors.SqlglotError):
            return {'state':'error','reason':'query_contract_invalid','message':'요청의 표·컬럼 또는 조회 조건을 현재 공개 자료와 맞추지 못했습니다. 데이터 사전의 표·컬럼을 확인하고 요청을 구체화해주세요.'}

    def execute(self,session_id,schema_name,plan,public_task):
        try:
            sql,tables=compile_conditions(plan['conditions'],public_task)
            if plan.get('state')!='ready' or sql!=plan.get('sql'): raise ValueError('조건과 SQL 불일치')
            result=self.runner.execute(session_id,schema_name,sql,allowed_tables=tables)
            if result.get('status')!='success': return {'state':'error','result':result}
            saved=deepcopy(self.runner.get(session_id,result['execution_id']))
            if saved.get('sql')!=sql or saved.get('result',{}).get('status')!='success': raise ValueError('근거 불일치')
            return {'state':'success','sql':sql,'conditions':plan['conditions'],'result':result,'full_result':saved['result'],
                'saved_execution':saved,'attempts':[{'sql':sql,'result':result}]}
        except Exception:
            return {'state':'error','reason':'result_capture_failed','message':'조회 조건·저장 근거를 확인하지 못했습니다.'}
