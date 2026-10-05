"""Sequential request-v2 planning, staging, validation and atomic ready records."""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from fastapi import BackgroundTasks
from psycopg.types.json import Jsonb
from .store import now
from .errors import DomainError
from .task_contracts import RequestV2, RequestAction, RuleApproval, RuleSample
from .capabilities import initialize as initialize_capabilities, list_capabilities, approve, CAPABILITIES
from .task_planner import interpretation_messages, parse_interpretation, choose_scenario, assemble_plan, unsupported_request, PROMPT_VERSION
from .planning_diagnostics import diagnostic, InterpretationDiagnostic
from .package_validation import stage, grant, revoke, publish, validate_plan
from .training import contains_material

def uid(): return str(uuid.uuid4())
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class TaskGeneration:
    def __init__(self,training):
        self.training=training
        self.store=training.store
        self.slot=threading.Lock()

    def initialize(self):
        initialize_capabilities(self.store)
        with self.store.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS generation_jobs (request_id text PRIMARY KEY REFERENCES training_requests ON DELETE CASCADE, private jsonb NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS generation_actions (request_id text REFERENCES training_requests ON DELETE CASCADE,action_id text,signature text NOT NULL,payload jsonb NOT NULL,PRIMARY KEY(request_id,action_id))')
            conn.execute('CREATE TABLE IF NOT EXISTS generation_samples (sample_id text PRIMARY KEY,payload jsonb NOT NULL)')
            conn.execute("UPDATE training_requests SET payload=payload || %s WHERE payload->>'contract_version'='request-v2' AND payload->>'status' IN ('accepted','planning','preparing_data','validating')",(Jsonb({'status':'interrupted','error_code':'interrupted','error':'서버 재시작으로 중단되었습니다. 고정 계획으로 수동 재시도할 수 있습니다.','retry_allowed':True}),))
        # Reconcile staged/granted files that never reached a ready record.
        with self.store.connect() as conn:
            jobs=conn.execute('SELECT j.private,r.payload FROM generation_jobs j JOIN training_requests r USING(request_id)').fetchall()
        for job in jobs:
            if job['payload']['status']!='ready' and job['private'].get('package_id'):
                try:
                    package=self.training.catalog.load(job['private']['package_id'],'v1',allow_unvalidated=True)
                    revoke(package)
                except (ValueError,OSError,DomainError):
                    pass
        try:
            from .generation_cleanup import prune
            prune(self.store,self.training.catalog)
        except Exception:
            # Cleanup failure never publishes an unverified package or resets records.
            pass

    def read(self,rid):
        value=self.training.request(rid)
        return {k:v for k,v in value.items() if k not in ('body','interpretation','fixed','seed')}

    def recent(self):
        with self.store.connect() as conn:
            return [r['public'].get('semantic_signature',{}) for r in conn.execute("SELECT p.public FROM training_plans p JOIN attempts a USING(attempt_id) ORDER BY a.payload->>'started_at' DESC LIMIT 5")]

    def begin(self,data,tasks):
        if contains_material(data.message) or any(contains_material(v) for v in (data.goal,data.sql_level,data.time_condition) if v):
            raise DomainError('temporary_material','요청에는 학습 목표만 입력하세요. SQL·결과는 훈련의 임시 입력을 사용하세요.',422)
        body=data.model_dump()
        recommendation_change=None
        if data.recommendation_id:
            from .recommendations import recommend
            current=recommend(self.store,{'difficulty':data.difficulty if data.difficulty!='auto' else None,'task_kind':data.task_kind,'goal':data.goal,'intentional_repeat':data.intentional_repeat})
            if current['recommendation_id']!=data.recommendation_id:
                recommendation_change='근거 이력 또는 선택 조건이 바뀌어 추천을 다시 계산했습니다.'
        with self.store.connect() as conn:
            row=conn.execute('SELECT signature,payload FROM training_requests WHERE request_id=%s',(data.request_id,)).fetchone()
        if row:
            if row['signature']!=digest(body):
                raise DomainError('idempotency_conflict','같은 요청 ID에 다른 내용이 있습니다.',409)
            return self.read(data.request_id)
        if not self.slot.acquire(blocking=False):
            raise DomainError('busy','다른 출제 작업이 진행 중입니다. 현재 입력을 유지하고 다시 시도하세요.',409)
        try:
            value={'request_id':data.request_id,'contract_version':'request-v2','operation_id':uid(),'status':'accepted','revision':0,
                   'message':data.message,'body':body,'created_at':now(),'attempt_id':None,'states':[{'status':'accepted','at':now()}],
                   'error':None,'error_code':None,'retry_allowed':False,'planning_calls':0,'generation_attempts':0,'questions':[]}
            value['recommendation_change']=recommendation_change
            with self.store.connect() as conn:
                conn.execute('INSERT INTO training_requests VALUES(%s,%s,%s,NULL)',(data.request_id,digest(body),Jsonb(value)))
                conn.execute('INSERT INTO generation_jobs VALUES(%s,%s)',(data.request_id,Jsonb({'seed':int(hashlib.sha256(data.request_id.encode()).hexdigest()[:8],16),'plan_id':uid()})))
            tasks.add_task(self.prepare,data.request_id)
            return self.read(data.request_id)
        except Exception:
            self.slot.release()
            raise

    def update(self,rid,status,**fields):
        with self.store.connect() as conn:
            row=conn.execute('SELECT payload FROM training_requests WHERE request_id=%s FOR UPDATE',(rid,)).fetchone()
            value=row['payload']
            if value['status'] in ('cancelled','ready'):
                return False
            value.update(status=status,**fields)
            if status == 'failed':
                value.setdefault('failure_history', []).append({'at':now(), 'error_code':value.get('error_code'), 'error':value.get('error'), 'planning_calls':value['planning_calls'], 'failure_detail': value.get('failure_detail')})
            value['states'].append({'status':status,'at':now()})
            conn.execute('UPDATE training_requests SET payload=%s WHERE request_id=%s',(Jsonb(value),rid))
        self.training.event(event_type='request_state',operation_id=value['operation_id'],request_id=rid,status=status,rules_version='access-plan-v2',error_code=value.get('error_code'))
        return True

    def prepare_adaptive(self, rid, data, started):
        from . import adaptive_tasks as adaptive, telemetry
        with self.store.connect() as conn:
            fixed=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()['private']
        def call(messages, phase):
            current=self.training.request(rid)
            if current['planning_calls'] >= adaptive.PLANNING_LIMIT:
                raise DomainError('planning_limit','요청별 설계·적합성 검증 호출 상한에 도달했습니다.',422)
            if not self.update(rid,'planning',planning_calls=current['planning_calls']+1):
                raise DomainError('cancelled','취소된 출제 요청입니다.',409)
            op=telemetry.begin(self.store,'ai',request_id=rid,call_limit=adaptive.PLANNING_LIMIT,domain='adaptive',rules_version=adaptive.VERSION,prompt_version=phase)
            try:
                with self.training.ai_lock:
                    gap=max(0, float(os.getenv('QUALITY_CALL_INTERVAL_SECONDS','5')))
                    remaining=gap-(time.monotonic()-getattr(self,'adaptive_last_call_finished',0))
                    if remaining>0: time.sleep(min(remaining,10))
                    result=self.training.auth.review(messages)
                    self.adaptive_last_call_finished=time.monotonic()
            except Exception:
                telemetry.finish(self.store,op,'failed',error_code='provider_failure',usage_missing_reason='failed_call')
                raise
            usage=result.get('usage') or {}
            telemetry.finish(self.store,op,'completed' if result.get('state')=='completed' else 'failed',error_code=None if result.get('state')=='completed' else 'provider_failure',model_version=result.get('model'),input_tokens=usage.get('input_tokens'),output_tokens=usage.get('output_tokens'),usage_missing_reason=None if usage else 'not_reported',provider_diagnostic=result.get('provider_diagnostic'))
            return result
        if not fixed.get('adaptive_recipe') or not fixed.get('alignment'):
            def parse(result):
                try: return adaptive.preflight(adaptive.parse_recipe(result),fixed['seed'],data)
                except DomainError as exc:
                    fixed['schema_issues']=getattr(exc,'validation_issues',[])
                    fixed.setdefault('schema_reviews',[]).append({'planning_calls':self.training.request(rid)['planning_calls'],'issues':fixed['schema_issues']})
                    with self.store.connect() as conn:
                        conn.execute('UPDATE generation_jobs SET private=%s WHERE request_id=%s',(Jsonb(fixed),rid))
                    raise
            messages=adaptive.planning_messages(data,self.recent())
            result={'state':'completed','text':json.dumps(fixed['adaptive_recipe'],ensure_ascii=False)} if fixed.get('adaptive_recipe') else call(messages,'adaptive-design-v1')
            for repair_number in range(3):
                try:
                    recipe=parse(result)
                    break
                except DomainError as exc:
                    if exc.code!='plan_invalid' or repair_number==2 or adaptive.PLANNING_LIMIT-self.training.request(rid)['planning_calls']<2: raise
                    messages=messages+[
                        {'role':'assistant','content':result.get('text','')},
                        {'role':'user','content':'이 설계는 서버 schema/고정 데이터 검증에 실패했습니다. 전체 JSON을 수정하세요. 검증 오류: '+json.dumps(getattr(exc,'validation_issues',[]),ensure_ascii=False)+'. investigation metrics에는 conditions가 있고 minimum>0인 현상 표본 집계가 최소 하나 필요합니다. 정수 조건 value는 숫자, category 조건 value는 문자열. 첫 컬럼만 id. foreign_key의 table은 앞선 표이고 group은 그 표의 실제 그룹. 오류에서 요구한 그룹 참조는 해당 Table.groups.overrides의 foreign_key.group에서 명시하세요. timestamp_sequence는 앞선 entity_column/interval_column과 timezone 포함 start/end. 그룹 override는 컬럼의 generator.kind/table을 바꾸면 안 됩니다. JSON schema 밖 필드와 SQL·코드 금지.'}]
                    result=call(messages,'adaptive-design-repair-v1')
            if recipe.status=='clarify':
                questions=recipe.questions or ['원하는 분석 목표와 필요한 판단을 구체적으로 알려주세요.']
                self.update(rid,'needs_clarification',questions=questions,error=' '.join(questions),error_code='clarification_required')
                return None
            if recipe.status=='unsupported':
                raise DomainError('unsupported_scope','요청한 목표를 현재 데이터 생성 기능으로 충족할 수 없습니다. 목표·기간·판단 범위를 구체화해 주세요.',422)
            if recipe.labels_public and not re.search(r'정답|라벨|레이블|labeled|labelled|ground.truth',data.message+' '+(data.goal or ''),re.I):
                raise DomainError('goal_mismatch','정답 라벨 공개를 요청하지 않은 탐지 과제에 정답 분류를 제공할 수 없습니다.',422)
            questions=[]
            if data.difficulty!='auto' and data.difficulty!=recipe.difficulty: questions.append('문장과 선택한 난이도가 다릅니다. 원하는 난이도를 알려주세요.')
            if data.task_kind!='auto' and data.task_kind!=recipe.task_kind: questions.append('문장과 선택한 유형이 다릅니다. 원하는 과제 유형을 알려주세요.')
            if questions:
                self.update(rid,'needs_clarification',questions=questions,error=' '.join(questions),error_code='clarification_required')
                return None
            fixed.update(adaptive_recipe=recipe.model_dump(),plan_revision=self.training.request(rid)['revision'],package_id='generated-'+hashlib.sha256(rid.encode()).hexdigest()[:24])
            with self.store.connect() as conn: conn.execute('UPDATE generation_jobs SET private=%s WHERE request_id=%s',(Jsonb(fixed),rid))
        recipe=adaptive.Recipe.model_validate(fixed['adaptive_recipe'])
        if not fixed.get('alignment'):
            for review_number in range(2):
                result = call(adaptive.alignment_messages(data,recipe),'adaptive-alignment-v1')
                try:
                    fixed['alignment']=adaptive.validate_alignment(result)
                    fixed.setdefault('alignment_reviews', []).append({'passed':True})
                    break
                except DomainError as exc:
                    issues=getattr(exc,'validation_issues',[])
                    fixed.setdefault('alignment_reviews', []).append({'passed':False,'error_code':exc.code,'issues':issues})
                    with self.store.connect() as conn:
                        conn.execute('UPDATE generation_jobs SET private=%s WHERE request_id=%s',(Jsonb(fixed),rid))
                    remaining=adaptive.PLANNING_LIMIT-self.training.request(rid)['planning_calls']
                    if exc.code!='goal_mismatch' or review_number or fixed.get('alignment_repair_used') or remaining<2:
                        raise
                    fixed['alignment_repair_used']=True
                    with self.store.connect() as conn:
                        conn.execute('UPDATE generation_jobs SET private=%s WHERE request_id=%s',(Jsonb(fixed),rid))
                    messages=adaptive.planning_messages(data,self.recent())+[
                        {'role':'assistant','content':json.dumps(recipe.model_dump(),ensure_ascii=False)},
                        {'role':'user','content':'다음 검증 사유를 자료로 검토하고 원 요청의 주제·난이도·형식은 유지하면서 전체 JSON을 한 번 수정하세요. 필수 자료를 보강하거나 근거 없는 분석 요구를 제거하세요. 요약은 원본 derived_from에서 계산하고 시간·프로필 연결을 일관되게 만드세요. 검증 사유: '+json.dumps(issues,ensure_ascii=False)}]
                    correction=call(messages,'adaptive-alignment-repair-v1')
                    try:
                        recipe=parse(correction)
                    except DomainError as correction_error:
                        if correction_error.code!='plan_invalid' or adaptive.PLANNING_LIMIT-self.training.request(rid)['planning_calls']<2:
                            raise
                        messages += [
                            {'role':'assistant','content':correction.get('text','')},
                            {'role':'user','content':'적합성 수정안이 형식·고정 데이터 검증에 실패했습니다. 원 요청과 앞선 적합성 사유를 유지하고 다음 오류를 수정한 전체 JSON만 반환하세요: '+json.dumps(getattr(correction_error,'validation_issues',[]),ensure_ascii=False)}]
                        recipe=parse(call(messages,'adaptive-design-repair-v1'))
                    if recipe.status!='ready':
                        raise DomainError('goal_mismatch','검증 수정 후에도 요청을 충족하는 데이터 설계를 완료하지 못했습니다.',422)
                    if recipe.labels_public and not re.search(r'정답|라벨|레이블|labeled|labelled|ground.truth',data.message+' '+(data.goal or ''),re.I):
                        raise DomainError('goal_mismatch','검증 수정 과정에서 허용하지 않은 정답 라벨을 공개할 수 없습니다.',422)
                    if (data.difficulty!='auto' and recipe.difficulty!=data.difficulty) or (data.task_kind!='auto' and recipe.task_kind!=data.task_kind):
                        raise DomainError('goal_mismatch','검증 수정 과정에서 요청한 난이도·형식을 변경할 수 없습니다.',422)
                    fixed['adaptive_recipe']=recipe.model_dump()
                    with self.store.connect() as conn:
                        conn.execute('UPDATE generation_jobs SET private=%s WHERE request_id=%s',(Jsonb(fixed),rid))
            with self.store.connect() as conn: conn.execute('UPDATE generation_jobs SET private=%s WHERE request_id=%s',(Jsonb(fixed),rid))
        value=self.training.request(rid)
        if not self.update(rid,'preparing_data',generation_attempts=value['generation_attempts']+1): return None
        validation_op=telemetry.begin(self.store,'validation',request_id=rid,domain='adaptive',rules_version='adaptive-validation-v1')
        package=None
        try:
            package,public,private=adaptive.stage_adaptive(self.training.catalog.root,fixed['package_id'],recipe,fixed['seed'],fixed['plan_id'],fixed['plan_revision'])
            public['original_request']=data.message
            if not self.update(rid,'validating'):
                telemetry.finish(self.store,validation_op,'cancelled')
                return package,public,private
            grant(package)
            actual=self.training.runner.execute(rid,package.schema_name,private['sql'],allowed_tables=public['required_tables'])
            with self.training.runner.lock: self.training.runner.pending.pop(actual['execution_id'],None)
            from .evaluation import verify_evidence
            if verify_evidence({'saved_execution_id':'validation','result':actual},private['expected'])['status']!='verified':
                revoke(package)
                raise DomainError('validation_failed','학습자 읽기 전용 권한으로 요청별 기준 집계를 검증하지 못했습니다.',422)
            telemetry.finish(self.store,validation_op,'completed')
            return package,public,private
        except Exception:
            if package:
                try: revoke(package)
                except Exception: pass
            telemetry.finish(self.store,validation_op,'failed',error_code='validation_failed')
            raise

    def prepare(self,rid):
        started=time.monotonic()
        package=None
        generated=False
        from . import telemetry
        op=None
        validation_op=None
        failure_detail=None
        try:
            value=self.training.request(rid)
            data=RequestV2.model_validate(value['body'])
            op=telemetry.begin(self.store,'generation',operation_id=value['operation_id'],parent_operation_id=value.get('parent_operation_id'),request_id=rid,domain='adaptive' if data.data_mode=='adaptive' else 'access',requested_difficulty=data.difficulty,task_kind=data.task_kind,rules_version='access-plan-v2')
            if data.data_mode == 'adaptive':
                prepared = self.prepare_adaptive(rid, data, started)
                if prepared is None: return
                package, public, private = prepared
                generated = True
                with self.store.connect() as conn:
                    fixed = conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()['private']
            else:
                if unsupported_request(data):
                    raise DomainError('unsupported_scope','현재는 접속 데이터만 지원합니다. 기존 접속 데이터의 계산·집계 검토·설계·현상 조사 중에서 요청해 주세요.',422)
                self.update(rid,value['status'],first_attempt_at=value.get('first_attempt_at') or now())
                with self.store.connect() as conn:
                    fixed=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()['private']
                if not fixed.get('selection'):
                    if not self.update(rid,'planning'): return
                    if value['planning_calls']>=3:
                        raise DomainError('planning_limit','요청 설계 호출 상한에 도달했습니다. 새 요청을 작성하세요.',422)
                    self.update(rid,'planning',planning_calls=value['planning_calls']+1)
                    ai_op=telemetry.begin(self.store,'ai',request_id=rid,call_limit=3,domain='access',rules_version='access-plan-v2',prompt_version=PROMPT_VERSION)
                    result={}
                    try:
                        with self.training.ai_lock:
                            result=self.training.auth.review(interpretation_messages(data,list_capabilities(self.store),self.recent()))
                        selection=parse_interpretation(result,data)
                    except Exception as exc:
                        failure_detail=getattr(exc, 'failure_detail', None) or diagnostic('internal', [{'field':'output', 'kind':'unexpected'}])
                        failure_detail=InterpretationDiagnostic.model_validate(failure_detail).model_dump(exclude_none=True)
                        usage=result.get('usage') or {}
                        telemetry.finish(self.store,ai_op,'failed',error_code='provider_failure' if failure_detail['stage'] in {'provider','internal'} else 'format_invalid',failure_detail=failure_detail,model_version=result.get('model'),input_tokens=usage.get('input_tokens'),output_tokens=usage.get('output_tokens'),usage_missing_reason=None if usage else 'not_reported' if result.get('state')=='completed' else 'failed_call',provider_diagnostic=result.get('provider_diagnostic'))
                        raise
                    usage=result.get('usage') or {}
                    telemetry.finish(self.store,ai_op,'completed',model_version=result.get('model'),input_tokens=usage.get('input_tokens'),output_tokens=usage.get('output_tokens'),usage_missing_reason=None if usage else 'not_reported',provider_diagnostic=result.get('provider_diagnostic'))
                    if selection.unsupported:
                        self.update(rid,'failed',error_code='unsupported_scope',error='현재는 접속 데이터만 지원합니다. 입력을 수정해 새 요청으로 시작하세요.',retry_allowed=False)
                        return
                    if selection.questions:
                        self.update(rid,'needs_clarification',questions=selection.questions,error=' '.join(selection.questions),error_code='clarification_required')
                        return
                    fixed['selection']=selection.model_dump()
                    fixed['plan_revision']=value['revision']
                    fixed['scenario']=choose_scenario(fixed['selection'],self.recent(),data.intentional_repeat)
                    fixed['package_id']='generated-'+hashlib.sha256(rid.encode()).hexdigest()[:24]
                    with self.store.connect() as conn:
                        conn.execute('UPDATE generation_jobs SET private=%s WHERE request_id=%s',(Jsonb(fixed),rid))
                selection=fixed['selection']
                generated=data.data_mode=='generated'
                if generated:
                    cap=next(c for c in list_capabilities(self.store) if c['capability_id']==selection['capability_id'])
                    if cap['status']!='approved':
                        raise DomainError('capability_unapproved','이 생성 규칙은 사람의 표본 검토·승인 대기입니다. 기존 데이터를 선택할 수 있습니다.',409)
                    if not self.update(rid,'preparing_data',generation_attempts=value['generation_attempts']+1): return
                    package=stage(self.training.catalog.root,fixed['package_id'],fixed['seed'],data.user_count,fixed['scenario'])
                else:
                    available=[p for p in self.training.catalog.list_public() if p['package_id']=='training-001']
                    if not available: raise DomainError('generation_unavailable','검증된 기존 데이터가 없습니다.',503)
                    chosen=max(available,key=lambda p:int(p['release_version'][1:]))
                    package=self.training.catalog.load(chosen['package_id'],chosen['release_version'])
                if not self.update(rid,'validating'): return
                validation_op=telemetry.begin(self.store,'validation',request_id=rid,domain='access',rules_version='access-validation-v2')
                public,private=assemble_plan(package,selection,fixed['plan_id'],fixed.get('plan_revision',0),fixed['scenario'],generated)
                from .evaluation import freeze_evaluation
                private['frozen_evaluation']=freeze_evaluation(public,private)
                if generated: grant(package)
                actual=self.training.runner.execute(rid,package.schema_name,private['sql'])
                with self.training.runner.lock:
                    self.training.runner.pending.pop(actual['execution_id'],None)
                if actual['status']!='success' or not actual['result_complete'] or len(actual['rows'])!=1:
                    raise DomainError('validation_failed','실제 DB 기준 계산 검증에 실패했습니다.',422)
                observed=dict(zip([c['name'] for c in actual['columns']],actual['rows'][0]))
                if any(observed.get(k)!=v and not (isinstance(v,float) and observed.get(k) is not None and abs(float(observed[k])-v)<=1e-6) for k,v in private['expected'].items()):
                    raise DomainError('validation_failed','독립 집계와 DB 계산이 다릅니다.',422)
                if public.get('analysis_draft'):
                    draft=self.training.runner.execute(rid,package.schema_name,public['analysis_draft'])
                    with self.training.runner.lock: self.training.runner.pending.pop(draft['execution_id'],None)
                    if draft['status']!='success': raise DomainError('validation_failed','검토 SQL을 실행할 수 없습니다.',422)
                if private.get('comparison_sql'):
                    from .evaluation import verify_comparison
                    comparison=self.training.runner.execute(rid,package.schema_name,private['comparison_sql'])
                    with self.training.runner.lock: self.training.runner.pending.pop(comparison['execution_id'],None)
                    proof={'saved_execution_id':'validation','result':comparison}
                    if verify_comparison(proof,private['comparison_expected'])['status']!='verified': raise DomainError('validation_failed','집단 비교 SQL과 독립 집계가 다릅니다.',422)
            if time.monotonic()-started>300: raise DomainError('preparation_timeout','출제 준비 시간 상한을 넘었습니다.',503)
            if validation_op: telemetry.finish(self.store,validation_op,'completed')
            with self.store.connect() as conn:
                job=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()
                if job and job['private'].get('package_id'):
                    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(job['private']['package_id'],))
                row=conn.execute('SELECT payload FROM training_requests WHERE request_id=%s FOR UPDATE',(rid,)).fetchone()
                value=row['payload']
                if value['status']=='cancelled': return
                if generated: publish(package)
                aid=uid()
                content={k:package.public[k] for k in ('package_id','release_version','dataset_id')}
                content.update(attempt_id=aid,problem_id='problem-001',contract_version='request-v2',started_at=now(),explanation_viewed=False,
                    task_kind=public['task_kind'],difficulty=public['difficulty'],domain=public['semantic_signature']['domain'],request_id=rid,title=public['title'],
                    plan_hash=digest(public),semantic_signature=public['semantic_signature'],intentional_repeat=data.intentional_repeat,
                    ai_calls=value['planning_calls'],evaluation_rules_version=public['evaluation_version'])
                conn.execute('INSERT INTO attempts(attempt_id,payload) VALUES(%s,%s)',(aid,Jsonb(content)))
                conn.execute('INSERT INTO training_plans VALUES(%s,%s,%s)',(aid,Jsonb(public),Jsonb(private)))
                value.update(status='ready',attempt_id=aid,error=None,error_code=None,retry_allowed=False,finished_at=now(),duration_ms=round((time.monotonic()-started)*1000))
                value['states'].append({'status':'ready','at':now()})
                conn.execute('UPDATE training_requests SET payload=%s,attempt_id=%s WHERE request_id=%s',(Jsonb(value),aid,rid))
            self.training.operational_event('task_published',aid,request_id=rid,rules_version='access-plan-v2')
        except DomainError as exc:
            current=self.training.request(rid)
            from .adaptive_tasks import PLANNING_LIMIT
            limit=PLANNING_LIMIT if data.data_mode=='adaptive' else 3
            retry_allowed=exc.code not in ('unsupported_scope','planning_limit')
            if data.data_mode=='adaptive':
                with self.store.connect() as conn:
                    fixed=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()['private']
                retry_allowed=retry_allowed and (bool(fixed.get('alignment')) or (current['planning_calls']<limit-1 and not fixed.get('alignment_repair_used')))
            self.update(rid,'failed',error_code=exc.code,error=exc.message,retry_allowed=retry_allowed,failure_detail=failure_detail)
        except Exception:
            self.update(rid,'failed',error_code='generation_failed',error='출제 작업에 실패했습니다. 원 입력과 고정 계획을 보존했습니다.',retry_allowed=True,failure_detail=failure_detail)
        finally:
            if generated and package and self.training.request(rid)['status']!='ready':
                try: revoke(package)
                except Exception: pass
            try:
                if validation_op:
                    with self.store.connect() as conn:
                        active=conn.execute("SELECT 1 FROM quality_operations WHERE operation_id=%s AND payload->>'status'='running'",(validation_op,)).fetchone()
                    if active: telemetry.finish(self.store,validation_op,'failed',error_code='validation_failed')
                if op:
                    final=self.training.request(rid)
                    status='completed' if final['status'] in {'ready','needs_clarification'} else 'cancelled' if final['status']=='cancelled' else 'failed'
                    telemetry.finish(self.store,op,status,error_code=('provider_failure' if failure_detail['stage'] in {'provider','internal'} else 'format_invalid') if status=='failed' and failure_detail else 'validation_failed' if status=='failed' else None, **({'failure_detail':failure_detail} if status=='failed' and failure_detail else {}))
            finally:
                self.slot.release()

    def action(self,rid,data,tasks,clarify=False):
        signature=digest(data.model_dump())
        with self.store.connect() as conn:
            previous=conn.execute('SELECT * FROM generation_actions WHERE request_id=%s AND action_id=%s',(rid,data.action_id)).fetchone()
            if previous:
                if previous['signature']!=signature: raise DomainError('idempotency_conflict','같은 행동 ID에 다른 입력입니다.',409)
                return self.read(rid)
        if clarify and (not data.message or contains_material(data.message)):
            raise DomainError('invalid_clarification','추가 답변에는 학습 목표와 선택 조건만 적어주세요.',422)
        if not self.slot.acquire(blocking=False): raise DomainError('busy','다른 출제 작업이 진행 중입니다.',409)
        try:
            with self.store.connect() as conn:
                job=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()
                if job and job['private'].get('package_id'):
                    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(job['private']['package_id'],))
                row=conn.execute('SELECT payload FROM training_requests WHERE request_id=%s FOR UPDATE',(rid,)).fetchone()
                if not row: raise DomainError('not_found','요청을 찾을 수 없습니다.',404)
                value=row['payload']
                if value['revision']!=data.expected_revision: raise DomainError('revision_conflict','최신 요청 revision으로 다시 확인하세요.',409)
                if clarify:
                    if value['status']!='needs_clarification': raise DomainError('invalid_state','추가 질문 대기 상태에서만 수정할 수 있습니다.',409)
                    from .adaptive_tasks import PLANNING_LIMIT
                    limit=PLANNING_LIMIT if value['body'].get('data_mode')=='adaptive' else 3
                    if value['planning_calls']>=limit: raise DomainError('planning_limit','설계 호출 상한에 도달했습니다. 입력을 보완해 새 요청으로 시작하세요.',409)
                    value['body']['message'] += '\n추가 답변: '+data.message
                    if data.difficulty: value['body']['difficulty']=data.difficulty
                    if data.task_kind: value['body']['task_kind']=data.task_kind
                else:
                    if value['status'] not in ('failed','interrupted') or not value.get('retry_allowed'): raise DomainError('invalid_state','현재 요청은 재시도할 수 없습니다.',409)
                    if value['generation_attempts']>=2: raise DomainError('retry_limit','최대 생성 시도에 도달했습니다. 새 요청을 작성하세요.',409)
                    if value['body'].get('data_mode')=='adaptive':
                        from .adaptive_tasks import PLANNING_LIMIT
                        fixed=conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',(rid,)).fetchone()['private']
                        if not fixed.get('alignment') and value['planning_calls']>=PLANNING_LIMIT-1:
                            raise DomainError('planning_limit','설계 호출 상한에 도달했습니다. 기존 실패 이유를 유지하며 입력을 보완해 새 요청으로 시작하세요.',409)
                value.update(status='accepted',revision=value['revision']+1,parent_operation_id=value['operation_id'],operation_id=uid(),error=None,error_code=None,failure_detail=None,questions=[])
                conn.execute('UPDATE training_requests SET payload=%s WHERE request_id=%s',(Jsonb(value),rid))
                conn.execute('INSERT INTO generation_actions VALUES(%s,%s,%s,%s)',(rid,data.action_id,signature,Jsonb({'revision':value['revision']})))
            tasks.add_task(self.prepare,rid)
            return self.read(rid)
        except Exception:
            self.slot.release()
            raise

    def sample(self,cap_id,data):
        cap=next((c for c in CAPABILITIES if c['capability_id']==cap_id),None)
        if not cap: raise DomainError('not_found','생성 능력을 찾을 수 없습니다.',404)
        if data.task_kind!=cap['task_kind']: raise DomainError('invalid_task_kind','선택한 규칙과 표본 유형이 다릅니다.',422)
        if not self.slot.acquire(blocking=False): raise DomainError('busy','출제 작업이 진행 중입니다.',409)
        try:
            sample_id=uid()
            package=stage(self.training.catalog.root,'sample-'+sample_id.replace('-',''),int(sample_id.replace('-','')[:8],16),data.user_count,cap['scenarios'][0])
            selection={'capability_id':cap_id,'task_kind':cap['task_kind'],'difficulty':data.difficulty if data.difficulty!='auto' else 'intermediate','goal':cap['goal'],'reason':'운영자 규칙 검토 표본'}
            public,private=assemble_plan(package,selection,uid(),0,cap['scenarios'][0],True)
            validate_plan(package,public,private)
            users,sessions=package.rows('users'),package.rows('sessions')
            payload={'sample_id':sample_id,'capability_id':cap_id,'rules_version':'access-rules-v2','status':'validated','public':public,'difficulty':selection['difficulty'],
                     'data_dictionary':package.public['data_dictionary'],'row_counts':{'users':len(users),'sessions':len(sessions)},'data_preview':{'users':users[:12],'sessions':sessions[:12]},
                     'created_at':now(),'package_id':package.public['package_id']}
            with self.store.connect() as conn: conn.execute('INSERT INTO generation_samples VALUES(%s,%s)',(sample_id,Jsonb(payload)))
            return payload
        finally: self.slot.release()

    def routes(self,app):
        @app.get('/api/training/capabilities')
        def capabilities():
            caps=list_capabilities(self.store)
            from .adaptive_tasks import PLANNING_LIMIT
            return {'capabilities':caps,'adaptive_data_enabled':True,'adaptive_status':'시험 과제 · 요청별 합성 데이터·자동 검증 · 사람 품질 판정 별도','generated_data_enabled':any(c['status']=='approved' for c in caps),'limits':{'users_min':50,'users_max':1000,'users_default':200,'sessions_max':10000,'adaptive_tables_max':4,'adaptive_rows_max':2000,'generation_attempts':2,'planning_calls':3,'adaptive_planning_calls':PLANNING_LIMIT}}
        @app.post('/api/training/requests/{rid}/clarify')
        def clarify(rid:str,data:RequestAction,tasks:BackgroundTasks): return self.action(rid,data,tasks,True)
        @app.post('/api/training/requests/{rid}/retry')
        def retry(rid:str,data:RequestAction,tasks:BackgroundTasks): return self.action(rid,data,tasks)
        @app.get('/api/training/capabilities/{cap_id}/samples')
        def samples(cap_id:str):
            with self.store.connect() as conn:
                return {'samples':[r['payload'] for r in conn.execute("SELECT payload FROM generation_samples WHERE payload->>'capability_id'=%s",(cap_id,))]}
        @app.post('/api/training/capabilities/{cap_id}/samples')
        def sample(cap_id:str,data:RuleSample): return self.sample(cap_id,data)
        @app.post('/api/training/capabilities/{cap_id}/approval')
        def approval(cap_id:str,data:RuleApproval): return approve(self.store,cap_id,data)
