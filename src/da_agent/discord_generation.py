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


def request(text, sid, difficulty):
    from .training import contains_material
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        raise DomainError('request_text', 'text에 연습하고 싶은 내용을 1~4000자로 입력하세요.')
    if contains_material(text):
        raise DomainError('temporary_material', 'text에는 학습 목표를 입력하세요. 보고서·SQL은 훈련에서 제출하세요.')
    return RequestV2(contract_version='request-v2', request_id=sid, message=text.strip(),
                     difficulty=difficulty, data_mode='adaptive')


def status_message(document):
    g = document['generation']
    labels = {'accepted':'출제 요청을 저장했습니다.', 'planning':'문제를 설계·검토하고 있습니다.',
        'preparing_data':'연습 자료를 준비하고 있습니다.', 'validating':'자료를 검산하고 있습니다.',
        'failed':'문제를 생성하지 못했습니다.', 'interrupted':'서버 재시작으로 출제가 중단됐습니다.',
        'cancelled':'출제를 취소했습니다.', 'needs_clarification':'출제 조건을 확인해주세요.'}
    lines = [labels.get(g['status'], g['status']), g.get('error', '')]
    if g['status'] == 'needs_clarification':
        lines += g.get('questions', []) + ['/answer text:답변으로 출제 조건을 알려주세요.']
    elif g['status'] in {'failed', 'interrupted'}:
        lines += ['기록은 보존했습니다. 재시도 버튼 또는 /retry로 수동 재시도하세요.']
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
    by_competency = {r.competency:r.completion for r in requirements}
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
    def __init__(self, service):
        self.service = service
        self.store = service.store

    def update(self, owner, sid, token, status, **fields):
        with self.store.edit(owner,sid) as (doc,_):
            g=doc['generation']
            if g.get('run_token') != token or g['status']=='cancelled':
                raise DomainError('cancelled','취소되거나 다른 실행으로 대체된 출제입니다.')
            g.update(status=status,**fields)
            g.setdefault('states',[]).append({'status':status,'at':timestamp()})
            doc['state']=status
        return doc

    def run(self, owner, sid, *, retry=False):
        from .discord_store import record_id
        token=record_id()
        with self.store.edit(owner,sid) as (doc,_):
            g=doc['generation']
            if g['status']=='ready' or g['status'] in ACTIVE or g['status']=='cancelled': return doc
            if g['status'] in {'failed','interrupted'} and not retry: return doc
            if g['status']=='needs_clarification': return doc
            g.update(status='planning',run_token=token,error=None,error_code=None)
            doc['state']='planning'
        job=self.store.generation_job(owner,sid)
        synthetic = getattr(self.service.provider, 'generation_mode', None) == 'synthetic'
        if synthetic and job.get('source_case', {}).get('version') != 'discord-synthetic-v1':
            # Preserve previous attempts for diagnosis but do not reuse a recipe
            # designed under the old searched-case contract after this switch.
            job.pop('recipe', None)
            job.pop('alignment', None)
            job.pop('alignment_repair_used', None)
            job['source_case'] = {'version':'discord-synthetic-v1', 'topic':'요청 기반 가상 분석',
                'business_problem':'사용자 요청에 맞춰 설정한 가상 업무 문제',
                'analysis_question':doc['generation']['message'], 'decision':'합성 자료로 판단과 한계를 연습',
                'selection_reason':'사용자 요청과 난이도로 Gemma가 직접 설계',
                'sources':[], 'searched_at':None, 'queries':[], 'search_suggestions':''}
        data=request(doc['generation']['message'],sid,doc['difficulty'])
        seed=int(hashlib.sha256(sid.encode()).hexdigest()[:8],16)
        package=None
        history=job.setdefault('calls',[])
        recent=[s['task'].get('semantic_signature',{}) for s in self.store.list(owner,doc['guild_id'])
                if s.get('generation',{}).get('status')=='ready' and s['session_id']!=sid][:5]

        def save(): self.store.save_generation_job(owner,sid,job)
        def design_messages():
            messages=adaptive.planning_messages(data,recent,None if synthetic else job['source_case'])
            if synthetic:
                messages[0]['content'] += (' 사용자 요청과 난이도로 가상 업무 상황을 직접 설계하세요. '
                    '반드시 단일 JSON 객체 {...}만 반환하고 배열 [{...}]로 감싸지 마세요. '
                    '최상위 reason 키는 status=ready에서도 필수입니다. difficulty_reason과 별개이며 설계 이유를 한 문장으로 작성하세요. '
                    '각 표의 columns[0].generator.kind만 id입니다. 두 번째 이후 컬럼에는 id를 쓰지 마세요. '
                    '파생 요약 표도 첫 summary_id 컬럼은 kind=id, 두 번째 account_id 등 집계키 컬럼은 kind=group_key입니다. '
                    'FK joins는 선언된 foreign_key 또는 그 값을 그대로 복사한 파생 group_key에서만 허용합니다. 일반 숫자/aggregate로 연결하지 마세요. '
                    'timestamp_sequence/offset에 필요한 duration/interval/source 컬럼을 시각 컬럼보다 먼저 선언하세요. '
                    '다른 표의 계정/대상 식별자는 generator={"kind":"foreign_key","table":"앞선표이름"}로 선언하세요. '
                    '각 groups[].count는 1~500, 모든 표의 그룹 count 합계는 2000 이하입니다. '
                    'count=1은 예시 자리표시자가 아닙니다. 실제 생성 행 수이므로 행동 비교 과제는 충분한 계정과 로그 표본을 설계하세요. '
                    '진단 conditions를 만족하는 행과 정상 반례를 groups의 overrides로 실제 생성하고, '
                    'group_by 비교는 적어도 두 값이 실제 데이터에 나타나도록 관측 속성을 그룹별 overrides로 보장하세요. '
                    '정상/의심 구분은 공개 행동 수치로 검토하게 하고 생성 그룹 이름을 category 값으로 노출하지 마세요. '
                    '공개 category에는 플랫폼·지역 등 관측 속성만 쓰며 normal/suspicious/bot/정상/의심 값과 is_bot 컬럼은 금지합니다. '
                    '실제 검색이나 출처 확인을 수행했다고 말하지 말고 회사 사례·인용·URL을 만들지 마세요. '
                    '업무 배경·기간·대상·수치는 연습용 가상 조건임을 공개 설명에 명시하세요.')
            messages[0]['content'] += (
                ' Discord 출제에서도 초급은 분석 대상·기간·단위와 판단 기준치를 공개하세요. '
                '중급은 요구한 비교 차원·필터가 검산 지표에 실제 연결되어야 합니다. '
                '고급은 업무 목표에서 학습자가 질문·우선순위를 정할 여지를 두세요. '
                '필수 판단을 미지원 표준편차·분산·체감·조치 효과로 만들지 마세요. '
                '관계 미확인·판단 유보·추가 관측도 타당한 결론으로 인정하고 무작위 효과·원인 존재를 전제하지 마세요.')
            return messages
        def call(messages,phase,method='review'):
            current=self.store.get(owner,sid)['generation']
            if current.get('run_token')!=token or current['status']=='cancelled':
                raise DomainError('cancelled','출제를 취소했습니다.')
            if current.get('planning_calls',0)>=adaptive.PLANNING_LIMIT:
                raise DomainError('planning_limit','요청별 출제 호출 한도에 도달했습니다. 새 /training 요청으로 시작하세요.')
            self.update(owner,sid,token,'planning',phase=phase,planning_calls=current.get('planning_calls',0)+1)
            self.store.reserve_call(owner,self.service.daily_limit)
            handler=getattr(self.service.provider,method,None)
            if not callable(handler): raise DomainError('research_unavailable','현재 모델 연결의 사례 검색·선정을 지원하지 않습니다.')
            entry={'phase':phase,'at':timestamp(),'state':'started'}
            history.append(entry); save()
            result=handler(messages)
            entry.update(state=result.get('state'),model=result.get('model'),usage=result.get('usage'),reason=result.get('reason'))
            if result.get('normalized_envelope'):
                entry['normalized_envelope'] = result['normalized_envelope']
            if isinstance(result.get('provider_diagnostic'), dict):
                entry['provider_diagnostic'] = deepcopy(result['provider_diagnostic'])
            if result.get('quota_diagnostic'):
                entry['quota_diagnostic'] = deepcopy(result['quota_diagnostic'])
            if isinstance(result.get('json_diagnostic'), dict):
                entry['json_diagnostic'] = deepcopy(result['json_diagnostic'])
            save()
            if result.get('state')!='completed':
                reason=result.get('reason','provider_unavailable')
                message='모델 호출에 실패해 출제를 보류했습니다. 호출 제한·모델 연결을 확인하고 수동 재시도하세요.'
                if reason=='research_unavailable': message='현재 제공자가 사례 검색을 지원하지 않습니다. 모델의 출제 방식을 확인하세요.'
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
                if retry and same_request:
                    previous = rejected[-1]
                    issues = previous['issues']
                    try:
                        adaptive.preflight(adaptive.parse_recipe({'state':'completed','text':previous['text']}),seed,data)
                    except (DomainError, ValueError) as exc:
                        issues = getattr(exc,'validation_issues',[{'message':str(exc)}])
                    messages += [{'role':'assistant','content':previous['text']},
                        {'role':'user','content':'이전 요청의 실패 설계를 보존했습니다. 원래 목표·난이도를 유지하고 다음 검증 오류를 수정한 전체 JSON 객체를 반환하세요. '+json.dumps(issues,ensure_ascii=False)}]
                for attempt in range(3):
                    result=call(messages,'adaptive-design')
                    try:
                        recipe=adaptive.preflight(adaptive.parse_recipe(result),seed,data)
                        if recipe.status=='ready': check_quality(recipe,recent,data.intentional_repeat)
                        break
                    except (DomainError,ValueError) as exc:
                        issues=getattr(exc,'validation_issues',[{'message':str(exc)}])
                        job.setdefault('design_errors',[]).append(issues)
                        # Keep rejected model recipes server-side for diagnosis;
                        # they must never become public task/evaluation material.
                        job.setdefault('rejected_designs',[]).append({'issues':issues,'text':result.get('text',''), 'request_message':data.message})
                        save()
                        if attempt==2: raise
                        messages += [{'role':'assistant','content':result.get('text','')},
                            {'role':'user','content':'원래 목표·난이도를 유지해 전체 JSON을 수정하세요. ratio에는 명시적인 분자 conditions가 필요하고 측정·비교 질문은 analysis metric_names에 연결해야 합니다. '+json.dumps(issues,ensure_ascii=False)}]
                if recipe.status=='clarify':
                    return self.update(owner,sid,token,'needs_clarification',questions=recipe.questions)
                if recipe.status=='unsupported': raise DomainError('unsupported_scope','현재 생성·검산 기능으로 요청 목표를 충족할 수 없습니다. 범위를 구체화해 새 요청을 입력하세요.')
                if recipe.difficulty!=data.difficulty:
                    return self.update(owner,sid,token,'needs_clarification',questions=['요청문과 선택 난이도가 다릅니다. 원하는 난이도를 알려주세요.'])
                if recipe.labels_public and not re.search(r'정답|라벨|레이블|labeled|labelled|ground.truth',data.message,re.I):
                    raise DomainError('goal_mismatch','정답 라벨 공개를 요청하지 않은 과제는 라벨을 공개할 수 없습니다.')
                job['recipe']=recipe.model_dump(); save()
            recipe=adaptive.Recipe.model_validate(job['recipe'])
            if not job.get('alignment'):
                for review_number in range(2):
                    try:
                        job['alignment']=adaptive.validate_alignment(call(adaptive.alignment_messages(data,recipe,seed,None if synthetic else job['source_case']),'adaptive-alignment'),quality_required=True)
                        save(); break
                    except DomainError as exc:
                        if exc.code!='goal_mismatch' or review_number or job.get('alignment_repair_used'): raise
                        job['alignment_repair_used']=True
                        issues=getattr(exc,'validation_issues',[])
                        job.setdefault('alignment_errors',[]).append(issues); save()
                        messages=design_messages()+[
                            {'role':'assistant','content':json.dumps(recipe.model_dump(),ensure_ascii=False)},
                            {'role':'user','content':'요청 목표·난이도를 유지하여 적합성 검토 오류를 수정한 전체 JSON을 반환하세요. '+json.dumps(issues,ensure_ascii=False)}]
                        original_labels=recipe.labels_public
                        recipe=adaptive.preflight(adaptive.parse_recipe(call(messages,'adaptive-alignment-repair')),seed,data)
                        if recipe.status!='ready' or recipe.difficulty!=data.difficulty or recipe.labels_public!=original_labels:
                            raise DomainError('goal_mismatch','검토 수정에서 요청 상태·난이도·공개 라벨을 변경할 수 없습니다.')
                        check_quality(recipe,recent,data.intentional_repeat)
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
            # Serialize final grants against cancellation; no model calls hold this lock.
            with self.store.edit(owner,sid) as (doc,_):
                if doc['generation'].get('run_token')!=token or doc['generation']['status']=='cancelled':
                    raise DomainError('cancelled','출제를 취소했습니다.')
                grant(package,admin_dsn=settings.admin_dsn,learner_role=learner.username)
                publish(package)
                doc.update(task=task,schema_name=package.schema_name,data_version=task['data_version'],state='analysis')
                doc['generation'].update(status='ready',package_id=job['package_id'],error=None)
                # Reserve a new stable name after the final title is known.
                for key in ('thread_name','thread_name_base','thread_name_ordinal'): doc.pop(key,None)
            return doc
        except Exception as exc:
            if package:
                try: revoke(package,admin_dsn=self.service.settings.admin_dsn,learner_role=urlparse(self.service.settings.learner_dsn).username)
                except Exception: pass
            current=self.store.get(owner,sid)
            if current['generation']['status']=='cancelled': return current
            code=exc.code if isinstance(exc,DomainError) else 'generation_failed'
            message=exc.message if isinstance(exc,DomainError) else '문제 설계·자료 검증에 실패했습니다. 검증되지 않은 과제는 공개하지 않았습니다.'
            job.setdefault('failures',[]).append({'code':code,'at':timestamp(),'issues':getattr(exc,'validation_issues',[])}); save()
            return self.update(owner,sid,token,'failed',error_code=code,error=message)
