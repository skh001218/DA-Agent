"""Owner-scoped Discord analysis lifecycle, independent of the running web app."""
import json
import time

from .discord_store import record_id, timestamp
from .errors import DomainError


REPORT_FIELDS = ('question', 'findings', 'hypothesis', 'alternatives', 'quality', 'limitations', 'action', 'next_checks')


class MeteredProvider:
    def __init__(self, provider, store, user_id, daily_limit, history):
        self.provider, self.store, self.user_id = provider, store, user_id
        self.limit, self.history = daily_limit, history

    def review(self, messages, **kwargs):
        try:
            self.store.reserve_call(self.user_id, self.limit)
        except DomainError as exc:
            self.history.append({'kind': 'limit', 'at': timestamp(), 'reason': exc.code, 'cost': None})
            return {'state': 'error', 'reason': exc.code}
        entry = {'kind': 'model', 'at': timestamp(), 'state': 'started', 'cost': None, 'usage': None}
        self.history.append(entry)
        started = time.monotonic()
        try:
            result = self.provider.review(messages, **kwargs)
            entry.update(state=result.get('state', 'error'), usage=result.get('usage'), reason=result.get('reason'))
            return result
        except Exception:
            entry.update(state='error', reason='provider_unavailable')
            return {'state': 'error', 'reason': 'provider_unavailable'}
        finally:
            entry['duration_ms'] = round((time.monotonic() - started) * 1000)


class DiscordTrainingService:
    def __init__(self, store, query_engine, provider, settings, dataset_factory=None):
        self.store, self.engine, self.provider, self.settings = store, query_engine, provider, settings
        self.dataset_factory = dataset_factory
        self.daily_limit = getattr(settings, 'daily_call_limit', 30)

    def get_session(self, user_id, session_id):
        return self.store.get(user_id, session_id)

    def list_sessions(self, user_id, guild_id):
        return self.store.list(user_id, guild_id)

    def start(self, user_id, guild_id, channel_id, event_id, topic='tutorial', difficulty='intermediate', help_level=None):
        from .discord_education import representative_task, prepare_dataset
        prior = self.store.claim_event(event_id, user_id, request={'action': 'start', 'topic': topic, 'difficulty': difficulty, 'guild_id': str(guild_id), 'channel_id': str(channel_id)})
        if prior is not None:
            return self.get_session(user_id, prior['session']['session_id']) if 'session' in prior else prior
        try:
            past = self.store.list(user_id, guild_id)
            variant = 'followup' if any(s['state'] == 'completed' for s in past) else 'baseline'
            task = representative_task(topic=topic, difficulty=difficulty, variant=variant)
            if help_level is not None:
                if help_level not in {'guided', 'independent'}:
                    raise DomainError('help_level', '도움 수준은 guided 또는 independent를 선택하세요.')
                task['help_policy']['default_level'] = help_level
            dataset = (self.dataset_factory or prepare_dataset)(self.settings, task)
            document = {'session_id': record_id(), 'owner_user_id': str(user_id), 'guild_id': str(guild_id),
                'channel_id': str(channel_id), 'thread_id': None, 'state': 'analysis', 'created_at': timestamp(),
                'task': task, 'schema_name': dataset['schema_name'], 'data_version': dataset.get('data_version', task.get('data_version')),
                'difficulty': difficulty, 'help_level': help_level or ('guided' if difficulty == 'beginner' else 'independent'),
                'messages': [], 'queries': [], 'executions': [], 'reports': [], 'evaluations': [], 'help_history': [],
                'telemetry': [], 'selected_evidence': [], 'pending_query': None, 'conditions': None, 'learning': []}
            self._message(document, 'stakeholder', str(task.get('objective', '공개 과제 조건을 확인하고 분석을 시작하세요.')), 'start')
            self.store.create(document, event_id, {'session': document})
            return document
        except Exception as exc:
            response = {'messages': [exc.message if isinstance(exc, DomainError) else '과제 준비에 실패했습니다. Discord 전용 DB 설정과 지원 주제를 확인하고 새 요청으로 다시 시작하세요.'], 'state': 'failed'}
            self.store.finish_event(event_id, response)
            return response

    def bind_thread(self, user_id, session_id, thread_id):
        with self.store.edit(user_id, session_id) as (document, conn):
            document['thread_id'] = str(thread_id)
        return self.get_session(user_id, session_id)

    def resume(self, user_id, guild_id, session_id=None):
        sessions = self.list_sessions(user_id, guild_id)
        document = self.get_session(user_id, session_id) if session_id else (sessions[0] if sessions else None)
        if not document:
            return {'messages': ['재개할 훈련이 없습니다. /training으로 시작하세요.']}
        if document['guild_id'] != str(guild_id):
            raise DomainError('forbidden', '이 서버의 훈련만 재개할 수 있습니다.', 403)
        task = document['task']
        public_intro = {key: task[key] for key in ('objective', 'period', 'timezone', 'dictionary', 'quality_information', 'rubric', 'help_policy', 'accepted_limits') if key in task}
        return {'session': document, 'messages': [f"훈련 {document['session_id']} · 상태 {document['state']} · 저장 조회 {len(document['executions'])} · 보고 버전 {len(document['reports'])}",
            '업무 담당자 · 공개 과제와 평가 조건: ' + json.dumps(public_intro, ensure_ascii=False),
            '주제는 tutorial을 지원합니다. /query로 자연어 조회, /help로 도움, /report로 보고, /followup으로 후속 답변, /submit으로 최종 제출하세요.']}

    def _message(self, document, role, text, event_id, help_type=None):
        item = {'id': record_id(), 'event_id': str(event_id), 'role': role, 'text': text, 'at': timestamp(), 'help_type': help_type}
        document['messages'].append(item)
        return item

    def handle(self, user_id, session_id, event_id, action, text='', payload=None):
        # Ownership is checked before any event lookup, query, model call or state write.
        self.get_session(user_id, session_id)
        prior = self.store.claim_event(event_id, user_id, session_id, {'action': action, 'text': text, 'payload': payload or {}})
        if prior is not None:
            return prior
        with self.store.edit(user_id, session_id) as (document, conn):
            self._message(document, 'user', text, event_id)
            try:
                messages = self._apply(document, event_id, action, text, payload or {})
            except DomainError as exc:
                messages = [exc.message]
                document['telemetry'].append({'kind': 'action_error', 'code': exc.code, 'at': timestamp(), 'action': action})
            except Exception:
                messages = ['요청 처리에 실패했습니다. 저장 기록을 재개해 확인하고 새 요청으로 다시 시도하세요.']
                document['telemetry'].append({'kind': 'action_error', 'code': 'internal', 'at': timestamp(), 'action': action})
            response = {'messages': messages, 'session': document}
            self.store.finish_event(event_id, response, conn)
        return response

    def _apply(self, document, event_id, action, text, payload):
        from .discord_education import help_response, evaluate_report, growth_observation
        if action in ('sql', 'evidence'):
            execution = next((item for item in document['executions'] if item['execution_id'] == payload.get('execution_id', text.strip())), None)
            if not execution or execution['result']['status'] != 'success':
                raise DomainError('evidence_missing', '이 훈련의 성공 조회 ID를 선택하세요.')
            if action == 'sql':
                document['help_history'].append({'type': 'sql_view', 'execution_id': execution['execution_id'], 'at': timestamp()})
                return [execution['sql']]
            if execution['execution_id'] not in document['selected_evidence']:
                document['selected_evidence'].append(execution['execution_id'])
            return [f"보고 근거로 선택했습니다: {execution['execution_id']}"]
        if action == 'end':
            if document['state'] != 'completed':
                document['previous_state'] = document['state']
                document['state'] = 'stopped'
            return ['훈련 상태와 모든 근거를 저장했습니다. /resume로 다시 확인할 수 있습니다.']
        if action == 'continue':
            if document['state'] == 'stopped':
                document['state'] = document.get('previous_state', 'analysis')
            return ['저장된 기준으로 훈련을 이어갑니다.']
        if document['state'] in ('completed', 'stopped'):
            raise DomainError('state', '완료 또는 중단된 훈련입니다. 기록을 열람하거나 중단된 훈련을 재개하세요.')
        if action in ('note', 'hypothesis', 'quality', 'direction'):
            return ['판단과 이유를 대화 기록에 저장했습니다. 조회 또는 보고를 요청하면 이어서 진행합니다.']
        if action in ('help', 'review'):
            kind = 'intermediate_feedback' if action == 'review' else payload.get('help_type', 'concept_hint')
            help_kind = 'feedback' if action == 'review' else {'concept_hint': 'concept', 'analysis_direction_hint': 'direction', 'intermediate_feedback': 'feedback'}.get(kind, 'concept')
            answer = help_response(document['task'], text, help_kind)
            answer_text = answer if isinstance(answer, str) else str(answer.get('message', answer.get('text', answer)))
            document['help_history'].append({'type': kind, 'help_level': document['help_level'], 'before_message_id': document['messages'][-1]['id'], 'text': answer_text, 'at': timestamp()})
            self._message(document, 'mentor', answer_text, event_id, kind)
            return ['멘토: ' + answer_text]
        if action in ('query', 'clarify', 'message'):
            if action == 'message' and not document['pending_query'] and not any(word in text.lower() for word in ('보여', '조회', '계산', '비교', 'count', 'rate', 'retention')):
                return ['생각을 기록했습니다. 조회 요청은 /query, 도움은 /help, 보고는 /report로 진행할 수 있습니다.']
            return self._query(document, event_id, text)
        if action == 'report':
            content = payload.get('content') or {'report_text': text}
            if not isinstance(content, dict) or not any(isinstance(value, str) and value.strip() for value in content.values()):
                raise DomainError('report_empty', '분석 질문·발견·가설/대안·품질 점검·한계·대응을 작성하세요.')
            if payload.get('append') and document['reports']:
                previous = document['reports'][-1]['content']
                content = {**previous, **content, 'report_text': str(previous.get('report_text', '')) + '\n' + str(content.get('report_text', ''))}
            document['reports'].append({'id': record_id(), 'version': len(document['reports']) + 1, 'content': content,
                'evidence_refs': list(document['selected_evidence']), 'at': timestamp()})
            document['state'] = 'followup'
            question = '업무 담당자: 공개 업무 목표를 기준으로 제안한 대응의 우선순위와 실행 뒤 확인할 지표를 설명해주세요. 불확실한 설명은 어떻게 추가 확인하겠습니까?'
            self._message(document, 'stakeholder', question, event_id)
            return [f"보고 버전 {len(document['reports'])}을 저장했습니다.", question]
        if action == 'followup':
            if document['state'] != 'followup':
                raise DomainError('state', '보고를 작성한 뒤 후속 질문에 답할 수 있습니다.')
            document['reports'][-1].setdefault('followup_answers', []).append({'text': text, 'message_id': document['messages'][-1]['id'], 'at': timestamp()})
            document['state'] = 'reporting'
            return ['후속 답변을 저장했습니다. 보고를 수정하거나 /submit로 평가를 요청하세요.']
        if action == 'submit':
            if not document['reports']:
                raise DomainError('report_missing', '먼저 보고 초안을 작성하세요.')
            if document['state'] == 'followup':
                raise DomainError('followup_missing', '업무 담당자의 후속 질문에 답한 뒤 최종 제출하세요.')
            report = document['reports'][-1]
            provider = MeteredProvider(self.provider, self.store, document['owner_user_id'], self.daily_limit, document['telemetry'])
            evaluation = evaluate_report(provider, document['task'], report, document['messages'], document['executions'], document['help_history'])
            document['evaluations'].append({'id': record_id(), 'report_id': report['id'], 'at': timestamp(), 'result': evaluation})
            held = evaluation.get('held', evaluation.get('status') in ('held', 'error'))
            if held:
                document['state'] = 'reporting'
            else:
                document['state'] = 'completed'
            history = [item for session in self.store.list(document['owner_user_id'], document['guild_id']) for item in session.get('learning', [])]
            grades = {row['id']: row.get('grade') for row in evaluation.get('criteria', [])}
            observation = {'owner_user_id': document['owner_user_id'], 'task_id': document['task'].get('task_id'),
                'task_version': document['task'].get('version'), 'rubric_version': document['task'].get('rubric', {}).get('version'),
                'difficulty': document['difficulty'], 'help_history': document['help_history'], 'evaluation': evaluation,
                'held': held, 'comparability_review': 'pending', 'before_help': grades if not document['help_history'] and not held else {},
                'after_help': grades if document['help_history'] else {}, 'before_help_message_ids': [m['id'] for m in document['messages'] if m['role'] == 'user' and (not document['help_history'] or m['at'] < document['help_history'][0]['at'])], 'at': timestamp()}
            document['learning'].append(observation)
            growth = growth_observation(history + [observation])
            return ['리뷰어: ' + json.dumps(evaluation, ensure_ascii=False), '학습 관측: ' + json.dumps(growth, ensure_ascii=False)]
        raise DomainError('action', '지원하지 않는 행동입니다. /query·/help·/report·/submit을 사용하세요.')

    def _query(self, document, event_id, text):
        from .discord_query import DiscordQueryEngine
        pending = document['pending_query']
        provider = MeteredProvider(self.provider, self.store, document['owner_user_id'], self.daily_limit, document['telemetry'])
        engine = DiscordQueryEngine(provider, self.engine.runner, self.settings)
        original = pending['text'] if pending else text
        clarification = '\n'.join(pending.get('answers', []) + [text]) if pending else None
        plan = engine.resolve(original, document['task'], previous_conditions=document['conditions'],
            clarification=clarification, difficulty='beginner' if document['help_level'] == 'guided' else 'intermediate')
        query = {'id': record_id(), 'original_text': original, 'user_answer': text if pending else None,
            'plan': plan, 'event_id': str(event_id), 'at': timestamp()}
        document['queries'].append(query)
        if plan.get('state') == 'clarification':
            document['pending_query'] = {'text': original, 'plan': plan, 'answers': pending.get('answers', []) + [text] if pending else []}
            answer = plan.get('question', '기간·분자·분모·집계를 확정해주세요.')
            if plan.get('options'):
                answer += '\n선택지: ' + ' / '.join(str(option) for option in plan['options'])
            kind = plan.get('help_type', 'request_confirmation')
            if kind != 'request_confirmation':
                document['help_history'].append({'type': kind, 'text': answer, 'at': timestamp()})
            self._message(document, 'mentor', answer, event_id, kind)
            return [answer]
        if plan.get('state') != 'ready':
            if plan.get('reason') == 'usage_limit':
                return ['오늘의 API 호출 한도에 도달했습니다. 기존 기록·SQL 열람·재개는 계속 사용할 수 있습니다.']
            if plan.get('reason') == 'api_rate_limited':
                return ['Gemma API 호출 한도(429)에 도달했습니다. 조회를 실행하지 않았으며 기록은 보존됩니다. 잠시 뒤 새 요청으로 다시 시도하세요.']
            return [plan.get('message', '조회 조건을 해석할 수 없었습니다. 지원하는 지표와 기간을 명시해 다시 요청하세요.')]
        document['pending_query'] = None
        document['conditions'] = plan.get('conditions')
        outcome = engine.execute(document['session_id'], document['schema_name'], plan, document['task'])
        query['outcome'] = outcome
        result = outcome.get('full_result', outcome.get('saved_execution', {}).get('result', outcome.get('result', {})))
        document['telemetry'].append({'kind': 'sql', 'state': result.get('status'), 'attempts': len(outcome.get('attempts', [])), 'duration_ms': result.get('duration_ms'), 'at': timestamp()})
        if outcome.get('state') != 'success' or result.get('status') != 'success':
            if outcome.get('reason') == 'result_capture_failed':
                return ['조회는 실행됐지만 전체 결과의 영구 근거 확보에 실패했습니다. 성공 근거로 저장하지 않았습니다. 새 요청으로 재실행하세요.']
            return ['조회 실패: ' + str((result.get('error') or {}).get('message', outcome.get('message', 'DB 연결과 조회 조건을 확인하세요.')))]
        execution = {'id': result['execution_id'], 'execution_id': result['execution_id'], 'status': 'success', 'sql': outcome.get('sql', plan.get('sql')), 'result': result,
            'conditions': plan['conditions'], 'query_id': query['id'], 'data_version': document['data_version'], 'at': timestamp()}
        document['executions'].append(execution)
        messages = [format_result(execution, self.settings)]
        if not document.get('direction_prompted'):
            document['direction_prompted'] = True
            question = '멘토: 이 비교를 선택한 이유와 검토할 가설을 설명해주세요. 어떤 결과가 나오면 가설을 수정하거나 기각하겠습니까? 이미 설명한 내용은 보고에도 연결할 수 있습니다.'
            self._message(document, 'mentor', question, event_id, 'request_confirmation')
            messages.append(question)
        return messages


def format_result(execution, settings):
    result = execution['result']
    rows = result.get('rows', [])
    preview = rows[:10]
    columns = [column['name'] if isinstance(column, dict) else str(column) for column in result.get('columns', [])]
    lines = [f"실행 성공 · ID {execution['execution_id']}", '확정 계산 기준: ' + json.dumps(execution['conditions'], ensure_ascii=False),
        '컬럼: ' + ' | '.join(columns)]
    lines.extend(' | '.join('NULL (값 없음/분모 0)' if value is None else str(value) for value in row) for row in preview)
    if not rows:
        lines.append('빈 결과입니다. 원인을 단정하거나 0%로 해석하지 않습니다.')
    if len(rows) > 10:
        lines.append(f'표시만 첫 10행으로 제한했습니다. 저장 수집 행: {len(rows)}.')
    if not result.get('result_complete', False):
        lines.append(f'수집 제한으로 불완전한 결과입니다. 최대 {settings.max_rows}행 / {settings.max_bytes}바이트. 전체 건수는 알 수 없습니다.')
    lines.append(f'읽기 전용 · 실행 제한 {settings.query_timeout_ms / 1000:g}초 · 데이터 버전 {execution["data_version"]} · /sql로 SQL 확인, /evidence로 보고 연결')
    return '\n'.join(lines)
