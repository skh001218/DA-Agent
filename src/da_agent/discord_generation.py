"""Discord persistence adapter for the shared adaptive generation primitives."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from . import adaptive_tasks as adaptive, case_research
from .errors import DomainError
from .package_validation import grant, publish, revoke
from .task_contracts import RequestV2
from .task_quality import check_quality
from .discord_store import timestamp

VERSION = 'discord-generation-v1'
ACTIVE = {'planning', 'preparing_data', 'validating'}


def failure_message(code, diagnostic=None):
    from .codex_provider import CLI_ERRORS
    if code in CLI_ERRORS:
        return CLI_ERRORS[code]
    messages = {
        'api_input_budget':'내부 출제 입력이 허용 크기를 초과했습니다. 초안과 원래 요청은 보존했습니다. 운영자의 입력 구성 점검이 필요하며 같은 요청을 그대로 반복하지 마세요.',
        'api_rate_limited':'분당 모델 호출·입력 한도로 출제를 보류했습니다. 대기 후 /retry로 수동 재시도하세요.',
        'usage_limit':'오늘의 모델 호출 한도에 도달했습니다. 다음 날 /retry로 재시도할 수 있으며 기존 기록은 계속 열람할 수 있습니다.',
        'planning_limit':'요청별 출제 호출 한도에 도달했습니다. 기록을 보존했으며 새 /training 요청이 필요합니다.',
        'plan_invalid':'모델의 자료 설계가 생성·관계 규칙을 충족하지 못했습니다. 초안을 보존했으며 /retry로 오류에 맞춘 수정을 요청할 수 있습니다.',
        'research_unavailable':'현재 제공자가 사례 검색을 지원하지 않습니다. 모델의 출제 방식을 확인하세요.',
        'api_key_missing':'모델 연결 키가 설정되지 않았습니다. 운영자가 연결 설정을 확인한 뒤 재시도하세요.',
        'api_key_invalid':'모델 연결 인증이 실패했습니다. 운영자가 연결 설정을 확인한 뒤 재시도하세요.',
        'model_unavailable':'설정한 모델을 사용할 수 없습니다. 운영자가 모델 설정을 확인한 뒤 재시도하세요.',
    }
    message = messages.get(code, '모델 호출을 완료하지 못했습니다. 기록은 보존했으며 연결 복구 후 수동 재시도할 수 있습니다.')
    delay = (diagnostic or {}).get('retry_after_seconds')
    if code == 'api_rate_limited' and isinstance(delay, int):
        message += f' 최소 {delay}초 뒤 다시 시도하세요.'
    return message


def request(text, sid, difficulty):
    from .training import contains_material
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        raise DomainError('request_text', 'text에 연습하고 싶은 내용을 1~4000자로 입력하세요.')
    if contains_material(text):
        raise DomainError('temporary_material', 'text에는 학습 목표를 입력하세요. 보고서·SQL은 훈련에서 제출하세요.')
    return RequestV2(contract_version='request-v2', request_id=sid, message=text.strip(),
                     difficulty=difficulty, data_mode='adaptive')


def status_message(document):
    from .discord_progress import progress_message
    g = document['generation']
    labels = {'accepted':'출제 요청을 저장했습니다.', 'planning':'문제를 설계·검토하고 있습니다.',
        'preparing_data':'연습 자료를 준비하고 있습니다.', 'validating':'자료를 검산하고 있습니다.',
        'failed':'문제를 생성하지 못했습니다.', 'interrupted':'서버 재시작으로 출제가 중단됐습니다.',
        'cancelled':'출제를 취소했습니다.', 'needs_clarification':'출제 조건을 확인해주세요.'}
    lines = [progress_message(g), labels.get(g['status'], g['status']), g.get('error', '')]
    if g['status'] == 'needs_clarification':
        lines += g.get('questions', []) + ['/answer text:답변으로 출제 조건을 알려주세요.']
    elif g['status'] in {'failed', 'interrupted'}:
        if g.get('error_code') in {'api_input_budget','planning_limit','usage_limit','api_key_missing','api_key_invalid','model_unavailable'}:
            lines += ['기록은 보존했습니다. 위 원인을 해결한 뒤 다시 진행하세요.']
        else:
            lines += ['기록은 보존했습니다. 재시도 버튼 또는 /retry로 수동 재시도하세요.']
        if g.get('error_code') == 'api_rate_limited' and g.get('retry_at'):
            from datetime import datetime
            from zoneinfo import ZoneInfo
            lines += ['재시도 가능 시각: '+datetime.fromisoformat(g['retry_at']).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S KST')]
    elif g['status']=='accepted':
        lines += ['/retry로 저장된 요청의 생성을 시작하고 /end로 취소할 수 있습니다.']
    elif g['status'] in ACTIVE | {'accepted'}:
        lines += ['/resume으로 상태 확인, /end로 취소할 수 있습니다.']
    return '\n'.join(line for line in lines if line)


def task_from_recipe(recipe, public, source_case, help_level):
    """Only public schema, requirements and definitions cross into education."""
    from .discord_education import CRITERIA
    schema = {t.name:{c.name:adaptive.TYPES[adaptive.generator_type(c.generator)] for c in t.columns} for t in recipe.tables}
    dictionary = {t.name:{'unit':t.grain, 'columns':schema[t.name],
        'description':' / '.join(f'{c.name}: {c.description}' for c in t.columns)} for t in recipe.tables}
    from .analytical_metrics import foreign_key_target
    tables = {t.name:t for t in recipe.tables}
    relationships = []
    for t in recipe.tables:
        for c in t.columns:
            target = foreign_key_target(t.name,c.name,tables)
            if target:
                relationships.append({'table':t.name,'column':c.name,'target_table':target,
                    'target_column':tables[target].columns[0].name})
    requirements = recipe.business_case.requirements
    by_competency = {r.competency:r.completion+(
        '; 판단 기준: '+r.judgment.decision_rule+'; 인정할 한계: '+r.judgment.accepted_limit
        if r.judgment else '') for r in requirements}
    required = {
        'problem_definition':recipe.goal + '; 대상·기간·단위를 공개 조건과 연결',
        'metric_design':by_competency.get('measurement', by_competency.get('comparison','분석 질문에 맞는 지표·비교 조건 정의')),
        'hypothesis_review':by_competency.get('alternatives', '확인한 사실과 가설을 구분하고 타당한 대안 또는 미확인을 설명'),
        'evidence_interpretation':'저장 실행을 인용해 ' + by_competency.get('comparison',by_competency.get('measurement',recipe.goal)) + '; 관측 차이로 인과를 단정하지 않음',
        'decision_limits':by_competency.get('decision','공개 업무 목표에 맞는 다음 확인 행동 제안') + '; ' + by_competency.get('uncertainty','확인할 수 없는 범위와 판단 유보 설명')}
    # Keep the established Discord criteria IDs and evidence ownership policy.
    criteria = [{'id':i,'name':n,'weight':w,'required':required[i],
        'core_error':{'problem_definition':'요청과 다른 대상·기간으로 결론 확정',
            'metric_design':'분모·단위·비교 조건을 혼동해 결론 확정',
            'hypothesis_review':'반례를 무시하고 비공개 원인 맞히기로 가설 확정',
            'evidence_interpretation':'근거 없는 수치·인과 확정',
            'decision_limits':'관측하지 못한 조치 효과를 확정'}[i],
        'advanced':by_competency.get('confounding','추가 검증과 한계를 실제 근거로 설명')}
        for i,n,w,_,_ in CRITERIA]
    dates = [c.generator.start for t in recipe.tables for c in t.columns if c.generator.start]
    ends = [c.generator.end for t in recipe.tables for c in t.columns if c.generator.end]
    return {'generation_version':VERSION, 'task_id':public['package_id'], 'version':public['plan_version'],
        'data_version':public['dataset_id'], 'title':recipe.title,'topic':recipe.topic,
        'difficulty':recipe.difficulty,'objective':public['description'], 'schema':schema,'dictionary':dictionary,
        # Presentation-only copy of facts already included in the public description.
        # Never project generation groups, reference values or answer SQL here.
        'intro_sections':{
            'background':recipe.description,
            'context':[recipe.business_case.background, recipe.business_case.observed_problem],
            'decision':recipe.business_case.decision,
            'questions':[r.question for r in requirements],
            'conditions':recipe.business_case.agent_assumptions,
            'judgments':[('판단 방법: '+r.judgment.method+'; 관측/통제 열: '+', '.join(r.judgment.control_columns)
                +'\n완료 판단: '+r.judgment.decision_rule+'\n인정할 한계: '+r.judgment.accepted_limit)
                if r.judgment else '' for r in requirements]},
        'relationships':relationships,'timezone':'Asia/Seoul',
        'period':{'start':min(dates)[:10] if dates else None,'end':max(ends)[:10] if ends else None,
                  'observation_end':public['data_complete_before'],'description':recipe.business_case.observation_period},
        'metrics':{m.name:{'description':m.operation, 'table':m.table, 'column':m.column,
            'operation':m.operation,'group_by':m.group_by,'conditions':[c.model_dump() for c in m.conditions],
            'denominator_conditions':[c.model_dump() for c in m.denominator_conditions],
            'joins':[j.model_dump() for j in m.joins]} for m in recipe.metrics if m.purpose=='analysis'},
        'quality_information':{'collection':'요청별 합성 자료 · 자동 검증 통과 · 사람 품질 검토 전',
            'required_check':'공개 자료의 단위·중복·관측 조건 점검',
            'verification_scope':'저장 조회 값은 근거로 사용하지만 임의 자연어 주장의 자동 검산은 미지원'},
        'accepted_limits':recipe.limitations + ['관계 미확인·판단 유보·추가 관측도 타당한 결론으로 인정'],
        'valid_paths':[r.question for r in requirements],
        'rubric':{'version':VERSION,'passing_grade':3,'weights_total':100,'criteria':criteria,
            'grades':{'0':'내용 없음','1':'확인된 핵심 오류','2':'필수 조건 누락','3':'필수 조건 충족','4':'추가 근거·한계 검토'},
            'non_scoring':['SQL 작성 역량','문장 길이','조회 횟수'],'no_duplicate_penalty':True},
        'help_policy':{'default_level':help_level,'types':['clarification','concept','direction','feedback']},
        'source_case':deepcopy(source_case)}


class DiscordGeneration:
    def __init__(self, service, progress=None):
        self.service = service
        self.store = service.store
        self.progress = progress
        self.last_progress = None

    def notify(self, document):
        if self.progress is not None:
            from .discord_progress import snapshot
            progress = snapshot(document['generation'])
            if progress == self.last_progress:
                return
            self.last_progress = progress
            try:
                self.progress(progress)
            except Exception:
                pass  # Display failure cannot change generation or publication.

    def update(self, owner, sid, token, status, **fields):
        with self.store.edit(owner,sid) as (doc,_):
            g=doc['generation']
            if g.get('run_token') != token or g['status']=='cancelled':
                raise DomainError('cancelled','취소되거나 다른 실행으로 대체된 출제입니다.')
            g.update(status=status,**fields)
            from .discord_progress import COMPLETED_STEPS
            if status in COMPLETED_STEPS:
                g['completed_steps'] = COMPLETED_STEPS[status]
            g.setdefault('states',[]).append({'status':status,'at':timestamp()})
            doc['state']=status
        self.notify(doc)
        return doc

    def run(self, owner, sid, *, retry=False):
        from .discord_store import record_id
        token=record_id()
        with self.store.edit(owner,sid) as (doc,_):
            g=doc['generation']
            if g['status']=='ready' or g['status'] in ACTIVE or g['status']=='cancelled': return doc
            if g['status'] in {'failed','interrupted'} and not retry: return doc
            if g['status']=='needs_clarification': return doc
            g.update(status='planning',run_token=token,error=None,error_code=None,completed_steps=0)
            g.pop('retry_at',None)
            doc['state']='planning'
        self.notify(doc)
        job=self.store.generation_job(owner,sid)
        sql_practice = doc.get('practice') == 'sql'
        synthetic = sql_practice or getattr(self.service.provider, 'generation_mode', None) == 'synthetic'
        quality_checker = check_quality
        if sql_practice:
            from .discord_generated_sql import check_quality as quality_checker
        def preflight(result):
            return adaptive.preflight(adaptive.parse_recipe(result),seed,data,quality_checker=quality_checker)
        if synthetic and job.get('source_case', {}).get('version') != 'discord-synthetic-v1':
            # Preserve previous attempts for diagnosis but do not reuse a recipe
            # designed under the old searched-case contract after this switch.
            job.pop('recipe', None)
            job.pop('alignment', None)
            job.pop('alignment_repair_used', None)
            job['source_case'] = {'version':'discord-synthetic-v1', 'topic':'요청 기반 가상 분석',
                'business_problem':'사용자 요청에 맞춰 설정한 가상 업무 문제',
                'analysis_question':doc['generation']['message'], 'decision':'합성 자료로 판단과 한계를 연습',
                'selection_reason':'사용자 요청과 난이도로 선택한 모델이 직접 설계',
                'sources':[], 'searched_at':None, 'queries':[], 'search_suggestions':''}
        data=request(doc['generation']['message'],sid,doc['difficulty'])
        if retry and sql_practice and job.get('recipe') and job.get('failures',[]) and job['failures'][-1]['code']=='goal_mismatch':
            # A structurally valid recipe that failed semantic review needs a
            # new design, not repeated review of the identical rejected draft.
            job.setdefault('rejected_designs',[]).append(dict(text=json.dumps(job.pop('recipe'),ensure_ascii=False),
                issues=job['failures'][-1].get('issues',[]),request_message=data.message,semantic_failure=True))
            job.pop('alignment',None)
            job.pop('alignment_repair_used',None)
        seed=int(hashlib.sha256(sid.encode()).hexdigest()[:8],16)
        package=None
        history=job.setdefault('calls',[])
        recent=[s['task'].get('semantic_signature',{}) for s in self.store.list(owner,doc['guild_id'])
                if s.get('generation',{}).get('status')=='ready' and s['session_id']!=sid][:5]

        def save(): self.store.save_generation_job(owner,sid,job)
        def design_messages():
            if sql_practice:
                from .discord_generated_sql import planning_messages
                return planning_messages(data, recent)
            if synthetic:
                from .discord_design import planning_messages
                return planning_messages(data, recent)
            messages=adaptive.planning_messages(data,recent,None if synthetic else job['source_case'])
            messages[0]['content'] += (
                'business_case.requirements의 competency는 중복 없이 초급 measurement/decision, '
                '중급 comparison/uncertainty/decision, 고급 comparison/uncertainty/decision/alternatives/confounding을 포함하세요. '
                ' Discord 출제에서도 초급은 분석 대상·기간·단위와 판단 기준치를 공개하세요. '
                '중급은 요구한 비교 차원·필터가 검산 지표에 실제 연결되어야 합니다. '
                '고급은 업무 목표에서 학습자가 질문·우선순위를 정할 여지를 두세요. '
                '고급 confounding 요구에는 judgment(method, control_columns, decision_rule, accepted_limit)를 반드시 작성하세요. '
                'control_columns는 evidence의 table.column으로 쓰세요. conditional_comparison은 통제 열을 포함한 '
                '2차원 이상 group_by의 analysis metric_names에 연결하고 동일 조건 안의 비교를 요구하세요. '
                '자료로 식별할 수 없다면 non_identifiable과 관측 근거에 연결한 판단 유보 이유를 명시하세요. '
                '필수 판단을 미지원 표준편차·분산·체감·조치 효과로 만들지 마세요. '
                '관계 미확인·판단 유보·추가 관측도 타당한 결론으로 인정하고 무작위 효과·원인 존재를 전제하지 마세요.')
            return messages
        def call(messages,phase,method='review'):
            current=self.store.get(owner,sid)['generation']
            if current.get('run_token')!=token or current['status']=='cancelled':
                raise DomainError('cancelled','출제를 취소했습니다.')
            if current.get('planning_calls',0)>=adaptive.PLANNING_LIMIT:
                raise DomainError('planning_limit','요청별 출제 호출 한도에 도달했습니다. 새 /training 요청으로 시작하세요.')
            self.update(owner,sid,token,'planning',phase=phase)
            def reserve_call():
                # Serialize cancellation and both counters in the same records transaction.
                with self.store.edit(owner,sid) as (latest_doc,conn):
                    latest=latest_doc['generation']
                    if latest.get('run_token')!=token or latest['status']=='cancelled':
                        raise DomainError('cancelled','출제를 취소했습니다.')
                    if latest.get('planning_calls',0)>=adaptive.PLANNING_LIMIT:
                        raise DomainError('planning_limit',failure_message('planning_limit'))
                    if conn is None:
                        self.store.reserve_call(owner,self.service.daily_limit)
                    else:
                        self.store.reserve_call(owner,self.service.daily_limit,conn=conn)
                    latest['planning_calls']=latest.get('planning_calls',0)+1
            handler=getattr(self.service.provider,method,None)
            if not callable(handler): raise DomainError('research_unavailable','현재 모델 연결의 사례 검색·선정을 지원하지 않습니다.')
            entry={'phase':phase,'at':timestamp(),'state':'started'}
            history.append(entry); save()
            if getattr(type(self.service.provider),'supports_call_reservation',False) is True:
                result=handler(messages,before_send=reserve_call)
            else:
                reserve_call()
                result=handler(messages)
            entry.update(state=result.get('state'),model=result.get('model'),usage=result.get('usage'),reason=result.get('reason'))
            if result.get('retry_at'):
                entry['retry_at'] = result['retry_at']
                self.update(owner,sid,token,'planning',retry_at=result['retry_at'])
            if result.get('normalized_envelope'):
                entry['normalized_envelope'] = result['normalized_envelope']
            if isinstance(result.get('provider_diagnostic'), dict):
                entry['provider_diagnostic'] = deepcopy(result['provider_diagnostic'])
            if result.get('quota_diagnostic'):
                entry['quota_diagnostic'] = deepcopy(result['quota_diagnostic'])
            if isinstance(result.get('json_diagnostic'), dict):
                entry['json_diagnostic'] = deepcopy(result['json_diagnostic'])
            if isinstance(result.get('input_diagnostic'),dict):
                entry['input_diagnostic']=deepcopy(result['input_diagnostic'])
            save()
            if result.get('state')!='completed':
                reason=result.get('reason','provider_unavailable')
                message=failure_message(reason,result.get('input_diagnostic'))
                raise DomainError(reason,message)
            return result

        try:
            if not job.get('source_case'):
                if not job.get('research'):
                    job['research']=case_research.grounded_sources(call(case_research.search_messages(data,recent),'case-search','research')); save()
                job['source_case']=case_research.select_case(call(case_research.selection_messages(data,job['research'],recent),'case-selection','select_case'),job['research'],recent); save()
            if not job.get('recipe'):
                messages=design_messages()
                rejected = job.get('rejected_designs', [])
                # Prefer the most recent complete draft over a short malformed
                # retry envelope; retain all attempts in the private history.
                if retry:
                    for candidate in reversed(rejected):
                        try:
                            complete = bool(json.loads(candidate['text']).get('tables'))
                        except (ValueError, TypeError, AttributeError, KeyError):
                            complete = False
                        if complete:
                            rejected = [candidate]
                            break
                same_request = rejected and (rejected[-1].get('request_message') == data.message or (
                    'request_message' not in rejected[-1] and doc['generation'].get('revision',0)==0
                    and doc['generation'].get('original_message')==data.message))
                reusable = None
                if retry and same_request:
                    previous = rejected[-1]
                    issues = previous['issues']
                    try:
                        candidate = preflight({'state':'completed','text':previous['text']})
                        if candidate.status=='ready' and candidate.difficulty==data.difficulty and not previous.get('semantic_failure'):
                            quality_checker(candidate,recent,data.intentional_repeat)
                            reusable = candidate
                    except (DomainError, ValueError) as exc:
                        issues = getattr(exc,'validation_issues',[{'message':str(exc)}])
                    from .discord_design import repair_messages
                    messages=repair_messages(design_messages(),previous['text'],issues)
                recipe = reusable
                for attempt in range(0 if reusable else 3):
                    result=call(messages,'adaptive-design')
                    try:
                        recipe=preflight(result)
                        if recipe.status=='ready': quality_checker(recipe,recent,data.intentional_repeat)
                        break
                    except (DomainError,ValueError) as exc:
                        issues=getattr(exc,'validation_issues',[{'message':str(exc)}])
                        job.setdefault('design_errors',[]).append(issues)
                        # Keep rejected model recipes server-side for diagnosis;
                        # they must never become public task/evaluation material.
                        job.setdefault('rejected_designs',[]).append({'issues':issues,'text':result.get('text',''), 'request_message':data.message})
                        save()
                        if attempt==2: raise
                        from .discord_design import repair_messages
                        errors=job.get('design_errors',[])
                        repeated=len(errors)>1 and errors[-1]==errors[-2]
                        messages=repair_messages(design_messages(),result.get('text',''),issues,repeated=repeated)
                if recipe.status=='clarify':
                    return self.update(owner,sid,token,'needs_clarification',questions=recipe.questions)
                if recipe.status=='unsupported': raise DomainError('unsupported_scope','현재 생성·검산 기능으로 요청 목표를 충족할 수 없습니다. 범위를 구체화해 새 요청을 입력하세요.')
                if recipe.difficulty!=data.difficulty:
                    return self.update(owner,sid,token,'needs_clarification',questions=['요청문과 선택 난이도가 다릅니다. 원하는 난이도를 알려주세요.'])
                if recipe.labels_public and not re.search(r'정답|라벨|레이블|labeled|labelled|ground.truth',data.message,re.I):
                    raise DomainError('goal_mismatch','정답 라벨 공개를 요청하지 않은 과제는 라벨을 공개할 수 없습니다.')
                job['recipe']=recipe.model_dump(); save()
            recipe=adaptive.Recipe.model_validate(job['recipe'])
            def alignment_messages():
                if sql_practice:
                    from .discord_generated_sql import alignment_messages as sql_alignment
                    return sql_alignment(data,recipe,seed)
                return adaptive.alignment_messages(data,recipe,seed,None if synthetic else job['source_case'])
            if not job.get('alignment'):
                for review_number in range(2):
                    try:
                        job['alignment']=adaptive.validate_alignment(call(alignment_messages(),'adaptive-alignment'),quality_required=True)
                        save(); break
                    except DomainError as exc:
                        if exc.code!='goal_mismatch' or review_number or job.get('alignment_repair_used'): raise
                        job['alignment_repair_used']=True
                        issues=getattr(exc,'validation_issues',[])
                        job.setdefault('alignment_errors',[]).append(issues); save()
                        from .discord_design import repair_messages
                        messages=repair_messages(design_messages(),json.dumps(recipe.model_dump(),ensure_ascii=False),issues)
                        original_labels=recipe.labels_public
                        recipe=preflight(call(messages,'adaptive-alignment-repair'))
                        if recipe.status!='ready' or recipe.difficulty!=data.difficulty or recipe.labels_public!=original_labels:
                            raise DomainError('goal_mismatch','검토 수정에서 요청 상태·난이도·공개 라벨을 변경할 수 없습니다.')
                        quality_checker(recipe,recent,data.intentional_repeat)
                        job['recipe']=recipe.model_dump(); save()
            self.update(owner,sid,token,'preparing_data')
            settings=self.service.settings
            admin,learner=urlparse(settings.admin_dsn),urlparse(settings.learner_dsn)
            if (admin.hostname,admin.port,admin.path)!=(learner.hostname,learner.port,learner.path) or admin.username==learner.username or admin.path.strip('/') in {'records','training','postgres'}:
                raise DomainError('generation_database','Discord 전용 데이터 DB와 분리된 읽기 계정을 설정하세요.')
            import psycopg
            with psycopg.connect(settings.admin_dsn,connect_timeout=5) as conn:
                privileges=conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,pg_has_role(rolname,current_user,\'MEMBER\') FROM pg_roles WHERE rolname=%s',(learner.username,)).fetchone()
                if not privileges or any(privileges): raise DomainError('generation_database','학습 계정은 적재 관리자 권한을 가질 수 없습니다.')
            root=Path(getattr(settings,'generation_directory','.local/discord-generation'))
            job.setdefault('package_id','discord-generated-'+hashlib.sha256(sid.encode()).hexdigest()[:24]); save()
            package,public,private=adaptive.stage_adaptive(root,job['package_id'],recipe,seed,sid,doc['generation'].get('revision',0),
                admin_dsn=settings.admin_dsn,learner_role=learner.username)
            self.update(owner,sid,token,'validating')
            if synthetic:
                job['source_case']['topic'] = recipe.topic
                save()
            task=task_from_recipe(recipe,public,case_research.public_case(job['source_case']) if job['source_case'].get('version') else job['source_case'],doc['help_level'])
            task['semantic_signature']=public['semantic_signature']
            task['semantic_signature']['source_topic']=job['source_case']['topic']
            job['private_reference']=private; save()
            if sql_practice:
                from .discord_generated_sql import make_task, prepare_checks
                task = make_task(task,recipe,seed)
                job['sql_reference'] = prepare_checks(settings,package,recipe,seed)
                save()
            # Serialize final grants against cancellation; no model calls hold this lock.
            with self.store.edit(owner,sid) as (doc,_):
                if doc['generation'].get('run_token')!=token or doc['generation']['status']=='cancelled':
                    raise DomainError('cancelled','출제를 취소했습니다.')
                grant(package,admin_dsn=settings.admin_dsn,learner_role=learner.username)
                publish(package)
                doc.update(task=task,schema_name=package.schema_name,data_version=task['data_version'],state='analysis')
                if sql_practice:
                    doc.update(sql_checks=[{k:c[k] for k in ('case','schema_name')} for c in job['sql_reference']['checks']],
                        sql_attempts=[],sql_reply_targets={},sql_exposure='없음 확인',source_session_id=None)
                doc['generation'].update(status='ready',package_id=job['package_id'],error=None,completed_steps=3)
                # Reserve a new stable name after the final title is known.
                for key in ('thread_name','thread_name_base','thread_name_ordinal'): doc.pop(key,None)
            self.notify(doc)
            return doc
        except Exception as exc:
            if sql_practice and job.get('sql_reference'):
                try:
                    from .discord_generated_sql import revoke_checks
                    revoke_checks(self.service.settings,[c['schema_name'] for c in job['sql_reference']['checks'] if c['case']!='main'])
                except Exception: pass
            if package:
                try: revoke(package,admin_dsn=self.service.settings.admin_dsn,learner_role=urlparse(self.service.settings.learner_dsn).username)
                except Exception: pass
            current=self.store.get(owner,sid)
            if current['generation']['status']=='cancelled' or current['generation'].get('run_token')!=token:
                self.notify(current)
                return current
            code=exc.code if isinstance(exc,DomainError) else 'generation_failed'
            message=exc.message if isinstance(exc,DomainError) else '문제 설계·자료 검증에 실패했습니다. 검증되지 않은 과제는 공개하지 않았습니다.'
            job.setdefault('failures',[]).append({'code':code,'at':timestamp(),'issues':getattr(exc,'validation_issues',[])}); save()
            return self.update(owner,sid,token,'failed',error_code=code,error=message)
