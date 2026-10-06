"""Natural-language intent to bounded shared metric compiler, never model SQL."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS

from .adaptive_tasks import Metric
from .analytical_metrics import compile_metric
from .errors import DomainError


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
            '사용자 요청을 공개 자료의 집계 조건으로 해석하고 JSON만 반환하세요. SQL·결과 수치 생성 금지. '
            '조건 table,operation(count/distinct/sum/avg/min/max/ratio),column,conditions,joins,group_by,denominator_conditions만 허용. '
            '조건 항목 {column,operator(eq/gt/gte/lt/lte),value}. column은 table.column 또는 기본 표의 column. '
            'joins 항목 {table,source_column}은 공개 FK의 many-to-one 연결만 허용. group_by 최대3개. '
            'ratio는 해당 행 중 conditions 분자 / denominator_conditions로 제한한 전체 행 비율(0~1). '
            '유저 고유 수를 분모로 하는 복합 비율, 편차·분산 등 지원 밖 계산은 error/unsupported_query. '
            '기간 필터는 공개 시각 컬럼과 사용자 지정 경계를 사용하고 시간대를 포함한 ISO 문자열로 표현. '
            '모호한 기간·분모·단위는 clarification과 중립 질문으로 확인. 임의 기본 추론 금지. '
            'pending_query는 이전 미확정 의도이며 새 요청이 다른 조회이면 replaces_pending=true. '
            'clarification에는 question, ready에는 conditions 전체, error에는 reason=unsupported_query. 예시: '+json.dumps(example)},
            {'role':'user','content':json.dumps({'request':text,'public_task':public,'previous_conditions':previous_conditions,
                'clarification':clarification,'pending_query':pending_query},ensure_ascii=False)}]
        try:
            result=self.provider.review(messages)
            meta={k:result[k] for k in ('usage','model') if k in result}
            if result.get('state')!='completed': return dict(state='error',reason=result.get('reason','provider_unavailable'),**meta)
            parsed=json.loads(result['text'])
            if parsed.get('state')=='clarification' and isinstance(parsed.get('question'),str) and parsed['question'].strip():
                return dict(state='clarification',question=parsed['question'][:600],proposed_conditions={},help_type='request_confirmation',**meta)
            if parsed.get('state')!='ready':
                return dict(state='error',reason='unsupported_query',message='공개 표의 행 수·고유 수·합계·평균·최소·최대·행 비율과 최대3차원 그룹 집계를 지원합니다. 지원 밖 계산은 근사 실행하지 않습니다.',**meta)
            conditions=parsed['conditions']
            sql,tables=compile_conditions(conditions,public_task)
            return dict(state='ready',conditions=conditions,sql=sql,replaces_pending=parsed.get('replaces_pending') is True,**meta)
        except (ValueError,KeyError,TypeError,AttributeError,DomainError):
            return {'state':'error','reason':'query_contract_invalid','message':'공개 표·컬럼·FK·집계 조건을 확인할 수 없어 조회하지 않았습니다. 요청 범위를 구체화해주세요.'}

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
