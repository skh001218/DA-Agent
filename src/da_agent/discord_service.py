"""Owner-scoped Discord analysis lifecycle, independent of the running web app."""
import json
import time

from .discord_store import record_id, timestamp
from .errors import DomainError
from .discord_tables import dictionary_tables, is_dictionary_request, result_table
from .discord_results import build_submission, submission_summary


REPORT_FIELDS = ('question', 'findings', 'hypothesis', 'alternatives', 'quality', 'limitations', 'action', 'next_checks')
ANSWER_GUIDANCE = '\n이 메시지에 답장하거나 @DA-Agent로 답해주세요. 메시지를 읽을 수 없으면 /answer를 사용하세요. 새 조회는 /query로 시작하세요.'


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
    def __init__(self, store, query_engine, provider, settings, dataset_factory=None, quality_registry=None):
        self.store, self.engine, self.provider, self.settings = store, query_engine, provider, settings
        self.dataset_factory = dataset_factory
        self.daily_limit = getattr(settings, 'daily_call_limit', 30)
        from .evaluation_registry import QualityRegistry
        self.quality_registry = quality_registry or QualityRegistry(getattr(settings,'quality_profiles_directory',None))

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
            if topic not in {'tutorial', '튜토리얼'}:
                raise DomainError('unsupported_topic', '현재는 튜토리얼 완료율 분석만 지원합니다. /training의 주제에서 튜토리얼 완료율 분석을 선택하세요. 게임 내 재화 변동 분석은 아직 지원하지 않습니다.')
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

    def reserve_thread_name(self, user_id, session_id):
        return self.store.reserve_thread_name(user_id, session_id)

    def history(self, user_id, guild_id, page=1):
        from datetime import datetime, timedelta, timezone
        from .discord_thread_titles import thread_title, thread_title_base
        from .discord_transport import safe_chunks
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise DomainError('history_page', '페이지는 1 이상의 정수로 입력하세요.')
        sessions = self.list_sessions(user_id, guild_id)
        if not sessions:
            return ['아직 연습 기록이 없습니다. /training으로 시작하세요.']
        pages = (len(sessions) + 4) // 5
        if page > pages:
            return [f'총 {pages}페이지입니다. /history page:{pages}로 마지막 페이지를 확인하세요.']
        lines = [f'내 분석 연습 기록 · {page}/{pages}페이지 · 총 {len(sessions)}개']
        states = {'analysis': '분석 중', 'reporting': '보고 작성', 'followup': '후속 답변',
                  'completed': '완료', 'stopped': '중단', 'interrupted': '중단'}
        for document in sessions[(page - 1) * 5:page * 5]:
            title = document.get('thread_name') or thread_title(thread_title_base(document))
            try:
                created = datetime.fromisoformat(document['created_at']).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')
            except (KeyError, ValueError, TypeError):
                created = '날짜 미상'
            lines.extend(['', safe_chunks(title)[0] + f" · {created} · {states.get(document.get('state'), '상태 확인 필요')}"])
            evaluations = document.get('evaluations', [])
            if evaluations:
                entry = evaluations[-1]
                result = entry['result']
                lines.append('평가 보류' if result.get('held') else f"점수 {result.get('total', '미정')}/100")
                publication = document.get('result_publications', {}).get(entry['id'], {})
                if publication.get('status') == 'published' and str(document['guild_id']).isdecimal() and str(publication.get('post_id')).isdecimal():
                    lines.append(f"[결과 보기](https://discord.com/channels/{document['guild_id']}/{publication['post_id']})")
            lines.append(f"/resume session_id:{document['session_id']}")
        if page < pages:
            lines.extend(['', f'다음 기록: /history page:{page + 1}'])
        # Five bounded titles and IDs fit comfortably in one Discord response.
        return ['\n'.join(lines)]

    def bind_question(self, user_id, session_id, question_id, message_ids):
        with self.store.edit(user_id, session_id) as (document, conn):
            question = next((q for q in document.get('questions', []) if q['id'] == question_id), None)
            if question:
                question['discord_message_ids'] = list(dict.fromkeys(question.get('discord_message_ids', []) + message_ids))
                if (document.get('pending_question') or {}).get('id') == question_id:
                    document['pending_question'] = question

    def _ask(self, document, kind, text, event_id, help_type=None):
        self._close_question(document, 'superseded')
        text = text + ANSWER_GUIDANCE
        question = {'id': record_id(), 'kind': kind, 'text': text, 'state': 'waiting',
                    'discord_message_ids': [], 'at': timestamp()}
        document.setdefault('questions', []).append(question)
        document['pending_question'] = question
        self._message(document, 'stakeholder' if kind == 'followup' else 'mentor', text, event_id, help_type)
        return text

    @staticmethod
    def _close_question(document, state):
        current = document.get('pending_question')
        if current:
            current['state'] = state
            for question in document.get('questions', []):
                if question['id'] == current['id']:
                    question['state'] = state
        document['pending_question'] = None

    def _restore_legacy_question(self, document, event_id):
        if 'questions' in document:
            return
        document['questions'] = []
        pending = document.get('pending_query')
        if pending:
            self._ask(document, 'query_conditions', pending['plan'].get('question', '조회 조건을 알려주세요.'), event_id)
        elif document['state'] == 'followup':
            self._ask(document, 'followup', '제안한 대응의 우선순위와 추가 확인 방법을 설명해주세요.', event_id)
        else:
            latest = next((m for m in reversed(document['messages']) if m.get('role') != 'user' or m.get('text', '').strip()), None)
            if latest and latest['role'] == 'mentor' and latest['text'].startswith('멘토: 이 비교를 선택한 이유'):
                self._ask(document, 'analysis_reason', latest['text'], event_id)

    def _answer(self, document, event_id, text, payload):
        if not text.strip():
            raise DomainError('answer_empty', '질문에 대한 답변 내용을 입력해주세요.')
        question = document.get('pending_question')
        reference = payload.get('reply_to_message_id')
        if reference:
            target = next((q for q in document.get('questions', []) if str(reference) in q.get('discord_message_ids', [])), None)
            if not target or not question or target['id'] != question['id'] or target['state'] != 'waiting':
                raise DomainError('answer_stale', '이 메시지는 현재 답변을 기다리는 질문이 아닙니다. 최신 질문에 답장하거나 /answer를 사용하세요.')
        if not question:
            raise DomainError('answer_missing', '현재 답변을 기다리는 질문이 없습니다. 새 조회는 /query로 요청하세요.')
        if question['kind'] == 'query_conditions':
            return self._query(document, event_id, text)
        if question['kind'] == 'followup':
            return self._apply(document, event_id, 'followup', text, {})
        document.setdefault('analysis_answers', []).append({'question_id': question['id'], 'text': text, 'at': timestamp()})
        self._close_question(document, 'answered')
        return ['비교를 선택한 이유와 가설에 대한 답변을 기록했습니다. 추가 조회는 /query, 보고 작성은 /report로 진행하세요.']

    def resume(self, user_id, guild_id, session_id=None):
        sessions = self.list_sessions(user_id, guild_id)
        document = self.get_session(user_id, session_id) if session_id else (sessions[0] if sessions else None)
        if not document:
            return {'messages': ['재개할 훈련이 없습니다. /training으로 시작하세요.']}
        if document['guild_id'] != str(guild_id):
            raise DomainError('forbidden', '이 서버의 훈련만 재개할 수 있습니다.', 403)
        from .discord_presentation import task_intro
        messages = [task_intro(document)]
        if document['executions'] or document['reports'] or document['state'] != 'analysis':
            messages.append(f"상태 {document['state']} · 저장 조회 {len(document['executions'])} · 보고 {len(document['reports'])}")
        if document.get('pending_question'):
            messages.append(document['pending_question']['text'])
        response = {'session': document, 'messages': messages}
        if document.get('evaluations'):
            response['submission'] = build_submission(document)
        return response

    def result_submission(self, user_id, session_id, evaluation_id=None):
        return build_submission(self.get_session(user_id, session_id), evaluation_id)

    def result_publication(self, user_id, session_id, evaluation_id):
        document = self.get_session(user_id, session_id)
        return document.get('result_publications', {}).get(evaluation_id, {})

    def save_result_publication(self, user_id, session_id, evaluation_id, **changes):
        allowed = {'status', 'forum_id', 'post_id', 'error_code'}
        if not set(changes) <= allowed:
            raise DomainError('publication_fields', '게시 기록 필드를 확인하세요.')
        with self.store.edit(user_id, session_id) as (document, conn):
            if not any(e['id'] == evaluation_id for e in document.get('evaluations', [])):
                raise DomainError('result_missing', '이 훈련의 평가 기록을 선택하세요.')
            publication = document.setdefault('result_publications', {}).setdefault(evaluation_id, {})
            if changes.get('status') == 'creating' and publication.get('status') in {'creating', 'uncertain', 'partial', 'published'}:
                raise DomainError('result_publish_busy', '다른 요청이 이미 게시를 시작했습니다. /resume으로 기존 게시 기록을 확인하세요.')
            publication.update(changes, updated_at=timestamp())
        return dict(publication)

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
            self._restore_legacy_question(document, event_id)
            self._message(document, 'user', text, event_id)['action'] = action
            try:
                messages = self._apply(document, event_id, action, text, payload or {})
            except DomainError as exc:
                messages = [exc.message]
                document['telemetry'].append({'kind': 'action_error', 'code': exc.code, 'at': timestamp(), 'action': action})
            except Exception:
                messages = ['요청 처리에 실패했습니다. 저장 기록을 재개해 확인하고 새 요청으로 다시 시도하세요.']
                document['telemetry'].append({'kind': 'action_error', 'code': 'internal', 'at': timestamp(), 'action': action})
            response = {'messages': messages, 'session': document}
            if action == 'submit' and document.get('evaluations') and (document['evaluations'][-1].get('event_id') == str(event_id) or document['state'] == 'completed'):
                response['submission'] = build_submission(document)
            if action in ('query', 'message', 'answer', 'help') and (is_dictionary_request(text) or (payload or {}).get('help_type') == 'data_dictionary'):
                response['tables'] = dictionary_tables(document['task'], text)
            elif document['executions'] and document['queries'] and document['queries'][-1]['event_id'] == str(event_id):
                execution = document['executions'][-1]
                if execution['query_id'] == document['queries'][-1]['id']:
                    response['tables'] = [result_table(execution)]
            if document['queries'] and document['queries'][-1]['event_id'] == str(event_id):
                query = document['queries'][-1]
                response['request_state'] = query.get('outcome', query['plan']).get('state', 'error')
            self.store.finish_event(event_id, response, conn)
        return response

    def _apply(self, document, event_id, action, text, payload):
        from .discord_education import help_response, evaluate_report, growth_observation
        if action == 'question':
            from .discord_terms import explain_terms
            provider = MeteredProvider(self.provider, self.store, document['owner_user_id'], self.daily_limit, document['telemetry'])
            messages = explain_terms(text, document['task'], provider)
            answer = '\n\n'.join(messages)
            document['help_history'].append({'type': 'term_question', 'text': answer, 'at': timestamp()})
            self._message(document, 'mentor', answer, event_id, 'term_question')
            return messages
        if action in ('query', 'message', 'answer', 'help') and (is_dictionary_request(text) or payload.get('help_type') == 'data_dictionary'):
            return ['📚 데이터 사전 · 첨부한 표를 누르면 확대할 수 있습니다.']
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
        if action == 'help':
            from .discord_presentation import reference_info
            reference = reference_info(document['task'], payload.get('help_type'))
            if reference is not None:
                return [reference]
        if action == 'submit' and document['state'] == 'completed' and document.get('evaluations'):
            return [submission_summary(document['evaluations'][-1])]
        if document['state'] == 'stopped' or document['state'] == 'completed' and action != 'report':
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
        if action in ('answer', 'clarify'):
            return self._answer(document, event_id, text, payload)
        if action == 'message':
            if document.get('pending_question') or payload.get('reply_to_message_id'):
                return self._answer(document, event_id, text, payload)
            return ['현재 답변을 기다리는 질문이 없습니다. 새 조회는 /query, 보고는 /report로 요청하세요.']
        if action == 'query':
            document['pending_query'] = None
            self._close_question(document, 'superseded')
            return self._query(document, event_id, text)
        if action == 'report':
            revising = document['state'] == 'completed'
            content = payload.get('content') or {'report_text': text}
            if not isinstance(content, dict) or not any(isinstance(value, str) and value.strip() for value in content.values()):
                raise DomainError('report_empty', '분석 질문·발견·가설/대안·품질 점검·한계·대응을 작성하세요.')
            if payload.get('append') and document['reports']:
                previous = document['reports'][-1]['content']
                content = {**previous, **content, 'report_text': str(previous.get('report_text', '')) + '\n' + str(content.get('report_text', ''))}
            prior_report = document['reports'][-1] if document['reports'] else None
            document['reports'].append({'id': record_id(), 'version': len(document['reports']) + 1, 'content': content,
                'evidence_refs': list(document['selected_evidence']), 'at': timestamp()})
            if prior_report:
                document['reports'][-1]['previous_report_id'] = prior_report['id']
            document['state'] = 'followup'
            question = '업무 담당자: 공개 업무 목표를 기준으로 제안한 대응의 우선순위와 실행 뒤 확인할 지표를 설명해주세요. 불확실한 설명은 어떻게 추가 확인하겠습니까?'
            document['pending_query'] = None
            question = self._ask(document, 'followup', question, event_id)
            return [f"보고 버전 {len(document['reports'])}을 저장했습니다." + (' 이전 보고·평가는 보존됩니다. 새 후속 질문에 답한 뒤 /submit로 재평가하세요.' if revising else ''), question]
        if action == 'followup':
            if document['state'] != 'followup':
                raise DomainError('state', '보고를 작성한 뒤 후속 질문에 답할 수 있습니다.')
            document['reports'][-1].setdefault('followup_answers', []).append({'text': text, 'message_id': document['messages'][-1]['id'], 'at': timestamp()})
            document['state'] = 'reporting'
            self._close_question(document, 'answered')
            return ['후속 답변을 저장했습니다. 보고를 수정하거나 /submit로 평가를 요청하세요.']
        if action == 'submit':
            if not document['reports']:
                raise DomainError('report_missing', '먼저 보고 초안을 작성하세요.')
            if document['state'] == 'followup':
                raise DomainError('followup_missing', '업무 담당자의 후속 질문에 답한 뒤 최종 제출하세요.')
            report = document['reports'][-1]
            model=getattr(self.provider,'model',None)
            model=model if isinstance(model,str) else getattr(self.settings,'llm_model','unknown')
            profile=self.quality_registry.check(document['task'],model)
            latest=document['evaluations'][-1] if document['evaluations'] else None
            if latest and latest['report_id']==report['id'] and latest['result'].get('profile_score_hold') and profile['status']!='eligible':
                return [submission_summary(latest)]
            provider = MeteredProvider(self.provider, self.store, document['owner_user_id'], self.daily_limit, document['telemetry'])
            # Prior drafts remain stored, but their report/followup messages do
            # not masquerade as the final conclusion of a revised report.
            current_followups = {a['message_id'] for a in report.get('followup_answers', []) if a.get('message_id')}
            obsolete_followups = {a['message_id'] for r in document['reports'][:-1] for a in r.get('followup_answers', []) if a.get('message_id')} - current_followups
            old_texts = {r.get('content', {}).get('report_text') for r in document['reports'][:-1]}
            old_texts.discard(None)
            evaluation_messages = [m for m in document['messages'] if m['id'] not in obsolete_followups
                                   and m.get('action') != 'report'
                                   and not (m.get('action') is None and m['role'] == 'user' and m.get('text') in old_texts)]
            evaluation = evaluate_report(provider, document['task'], report, evaluation_messages, document['executions'], document['help_history'])
            from .evaluation_registry import with_profile_policy
            evaluation=with_profile_policy(evaluation,profile)
            previous = next((e for e in reversed(document['evaluations']) if e['report_id'] != report['id']), None)
            document['evaluations'].append({'id': record_id(), 'report_id': report['id'], 'event_id': str(event_id), 'at': timestamp(), 'result': evaluation})
            if previous:
                from .evaluation_quality import revision_feedback
                old_grades = {c['id']: c.get('grade') for c in previous['result'].get('criteria', [])}
                old_report = next(r for r in document['reports'] if r['id'] == previous['report_id'])
                document['evaluations'][-1]['revision_comparison'] = {
                    'previous_evaluation_id': previous['id'], 'previous_report_version': old_report['version'],
                    'report_version': report['version'],
                    'previous_total': previous['result'].get('total'), 'total': evaluation.get('total'),
                    'criteria': [{'id': c['id'], 'before': old_grades.get(c['id']), 'after': c.get('grade')}
                                 for c in evaluation.get('criteria', [])],
                    'previous_check_version': previous['result'].get('arithmetic_verification', {}).get('version'),
                    'check_version': evaluation.get('arithmetic_verification', {}).get('version'),
                    'errors_before': len(previous['result'].get('arithmetic_verification', {}).get('errors', [])) if 'arithmetic_verification' in previous['result'] else None,
                    'errors_after': len(evaluation.get('arithmetic_verification', {}).get('errors', [])),
                    'note': '동일 과제의 보고 수정 관측이며 학습 효과 입증은 아님'}
                document['evaluations'][-1]['revision_feedback'] = revision_feedback(previous['result'], evaluation)
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
            document['evaluations'][-1]['growth'] = growth
            return [submission_summary(document['evaluations'][-1])]
        raise DomainError('action', '지원하지 않는 행동입니다. /query·/help·/report·/submit을 사용하세요.')

    def _query(self, document, event_id, text, new_query=False):
        from .discord_query import DiscordQueryEngine
        if new_query:
            document['pending_query'] = None
            self._close_question(document, 'superseded')
        pending = document['pending_query']
        provider = MeteredProvider(self.provider, self.store, document['owner_user_id'], self.daily_limit, document['telemetry'])
        engine = DiscordQueryEngine(provider, self.engine.runner, self.settings)
        original = pending['text'] if pending else text
        if pending:
            pending.setdefault('answers', []).append(text)
        clarification = '\n'.join(pending['answers']) if pending else None
        context = {'request': original, 'question': pending['plan'].get('question'),
                   'proposed_conditions': pending['plan'].get('proposed_conditions', {}),
                   'answers': pending.get('answers', [])} if pending else None
        plan = engine.resolve(text, document['task'], previous_conditions=document['conditions'],
            clarification=clarification, pending_query=context,
            difficulty='beginner' if document['help_level'] == 'guided' else 'intermediate')
        query = {'id': record_id(), 'original_text': original, 'user_answer': text if pending else None,
            'submitted_text': text, 'plan': plan, 'event_id': str(event_id), 'at': timestamp()}
        document['queries'].append(query)
        if plan.get('replaces_pending'):
            query.update(original_text=text, user_answer=None)
            original, pending = text, None
        if plan.get('state') == 'clarification':
            document['pending_query'] = {'text': original, 'plan': plan, 'answers': pending['answers'] if pending else []}
            answer = plan.get('question', '기간·분자·분모·집계를 확정해주세요.')
            if plan.get('options'):
                answer += '\n선택지: ' + ' / '.join(str(option) for option in plan['options'])
            kind = plan.get('help_type', 'request_confirmation')
            if kind != 'request_confirmation':
                document['help_history'].append({'type': kind, 'text': answer, 'at': timestamp()})
            answer = self._ask(document, 'query_conditions', answer, event_id, kind)
            return [answer]
        if plan.get('state') != 'ready':
            if plan.get('reason') == 'unsupported_query':
                document['pending_query'] = None
                self._close_question(document, 'superseded')
            elif pending:
                document['pending_query'] = dict(pending, answers=pending['answers'])
            if plan.get('reason') == 'usage_limit':
                return ['오늘의 API 호출 한도에 도달했습니다. 기존 기록·SQL 열람·재개는 계속 사용할 수 있습니다.']
            if plan.get('reason') == 'api_rate_limited':
                return ['Gemma API 호출 한도(429)에 도달했습니다. 답변과 조회 조건은 보존됩니다. 잠시 뒤 /answer로 이어서 답해주세요. 새 조회는 /query로 시작하세요.']
            if plan.get('reason') in {'api_unavailable', 'api_timeout', 'api_key_invalid', 'api_permission_denied', 'api_request_invalid', 'api_invalid_response', 'api_empty_response', 'api_content_blocked', 'response_incomplete', 'provider_unavailable', 'provider_invalid_json', 'provider_invalid_response', 'api_key_missing', 'model_unavailable'}:
                return ['모델 서비스 오류로 조회를 실행하지 못했습니다. 답변과 조회 조건은 보존됩니다. 잠시 뒤 /answer로 이어서 답해주세요. 새 조회는 /query로 시작하세요.']
            return [plan.get('message', '조회 조건을 해석할 수 없었습니다. 지원하는 지표와 기간을 명시해 다시 요청하세요.')]
        document['pending_query'] = None
        self._close_question(document, 'answered')
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
            question = self._ask(document, 'analysis_reason', question, event_id, 'request_confirmation')
            messages.append(question)
        return messages


def format_task_intro(document):
    """Render public conditions as readable sections without revealing private task data."""
    task = document['task']
    period = task.get('period', {})
    state = {'analysis': '분석 중', 'followup': '후속 답변 대기', 'reporting': '보고 작성 중',
             'completed': '완료', 'stopped': '중단', 'interrupted': '중단'}.get(document['state'], document['state'])
    status = '\n'.join([
        '📌 ' + task.get('title', '분석 훈련'),
        f"상태: {state} · 저장 조회: {len(document['executions'])}개 · 보고: {len(document['reports'])}개",
        f"훈련 ID: {document['session_id']}",
    ])
    brief = '\n'.join([
        '🎯 업무 담당자 · 공개 과제와 평가 조건', task.get('objective', ''), '',
        '기간과 비교 대상',
        f"• 가입 기간: {period.get('start', '미정')}부터 {period.get('end', '미정')}까지"
        + (' (종료일 제외)' if period.get('end_exclusive') else ' (종료일 포함)'),
        f"• 시간 기준: {task.get('timezone', '미정')}",
        f"• 관측 종료: {period.get('observation_end', '미정')}",
    ])
    data = ['📚 데이터 사전']
    for name, table in task.get('dictionary', {}).items():
        data.extend(['', f"{name} — 한 행: {table.get('unit', '미정')}", table.get('description', ''),
                     '컬럼과 자료형은 아래 첨부 표에서 확인하세요.'])
    quality = ['🔎 신뢰성 확인과 해석 한계', task.get('quality_information', {}).get('collection', ''),
               '필수 점검: ' + task.get('quality_information', {}).get('required_check', '')]
    quality.extend('• ' + limit for limit in task.get('accepted_limits', []))
    rubric = task.get('rubric', {})
    assessment = ['📋 평가 기준']
    for criterion in rubric.get('criteria', []):
        assessment.extend(['', f"{criterion['name']} ({criterion['weight']}%)",
                           '필수: ' + criterion['required'], '핵심 오류: ' + criterion['core_error'],
                           '추가 검증: ' + criterion['advanced']])
    assessment.extend(['', '등급: ' + ' / '.join(f'{grade} = {meaning}' for grade, meaning in rubric.get('grades', {}).items()),
                       f"통과 기준: {rubric.get('passing_grade', '미정')}등급 · 기준 버전: {rubric.get('version', '미정')}",
                       '점수에 반영하지 않음: ' + ', '.join(rubric.get('non_scoring', []))])
    if rubric.get('no_duplicate_penalty'):
        assessment.append('같은 오류는 중복 감점하지 않습니다.')
    from .discord_verification import POLICY
    assessment.extend(['', '실행 근거 검산 정책', POLICY])
    policy = task.get('help_policy', {})
    commands = ['▶ 이 문제를 이어가는 방법', '이 과제 스레드 안에서 아래 명령을 사용하세요.', '',
                '1. /query — 조회 요청·확인 답변 (new_query를 켜면 이전 질문 초기화)',
                '2. /help — 개념·분석 방향·중간 피드백 요청',
                '3. /sql — 실행 SQL 확인 · /evidence — 조회를 보고 근거로 선택',
                '4. /report — 보고 작성·수정 (append로 긴 보고 이어 쓰기)',
                '5. /followup — 업무 담당자의 후속 질문에 답변',
                '6. /submit — 최종 제출과 평가', '',
                '/end — 중단·기록 보존 · /resume — 저장한 문제 재개',
                '지원 주제: 튜토리얼 완료율 분석',
                '기본 도움: ' + {'independent': '내 정의 먼저', 'guided': '안내 포함'}.get(policy.get('default_level'), '미정'),
                '도움 유형: ' + ', '.join({'clarification': '정의 확인', 'concept': '개념', 'direction': '분석 방향', 'feedback': '피드백'}.get(kind, kind) for kind in policy.get('types', []))]
    if policy.get('record_before_after'):
        commands.append('도움받기 전후의 답변을 기록합니다.')
    commands.append('대화로 질문·확인 답변을 보내려면 @DA-Agent를 선택해 멘션하세요. 일반 메시지 수신이 꺼져 있어도 멘션한 내용은 처리합니다.')
    return [status, brief, '\n'.join(data), '\n'.join(quality), '\n'.join(assessment), '\n'.join(commands)]


def format_result(execution, settings):
    result = execution['result']
    rows = result.get('rows', [])
    preview = rows[:10]
    labels = {'metric': '지표', 'period': '기간', 'start': '시작일', 'end': '종료일',
              'end_exclusive': '종료일 제외', 'numerator': '분자', 'denominator': '분모',
              'aggregation': '집계', 'group_by': '그룹', 'timezone': '시간 기준',
              'observation_end': '관측 종료', 'channel': '채널', 'step': '단계',
              'unit': '단위', 'completion_window': '완료 관측 범위', 'event_start': '이벤트 시작일',
              'event_end': '이벤트 종료일 (제외)', 'period_basis': '기간 기준', 'filters': '필터'}
    meanings = {'tutorial_rate': '튜토리얼 완료율', 'd7_retention': 'D7 재방문율',
                'weekly_return': '주간 재방문율', 'users_count': '가입 사용자 수',
                'attempts_count': '도전 이벤트 수', 'duplicate_attempts': '중복 도전 이벤트 수',
                'completed_users': '완료한 고유 사용자', 'signup_users': '가입한 고유 사용자',
                'attempted_users': '도전한 고유 사용자', 'user': '고유 사용자',
                'attempt': '도전 이벤트', 'explicit_dates': '명시한 날짜', 'channel': '유입 채널'}
    def describe(value):
        if isinstance(value, dict):
            return ', '.join(f'{labels.get(k, k)}: {describe(v)}' for k, v in value.items())
        if isinstance(value, list):
            return ', '.join(describe(v) for v in value)
        if isinstance(value, bool):
            return '예' if value else '아니오'
        return meanings.get(str(value), str(value))
    lines = [f"실행 성공 · ID {execution['execution_id']}", '확정 계산 기준']
    lines.extend(f'• {labels.get(key, key)}: {describe(value)}' for key, value in execution['conditions'].items())
    lines.append('조회 결과는 첨부 표에서 확인하세요. 표를 누르면 확대할 수 있습니다.')
    if any(value is None for row in preview for value in row):
        lines.append('NULL은 값 없음입니다. 비율의 분모 0 등 원인을 확인하며 0%로 해석하지 않습니다.')
    if not rows:
        lines.append('빈 결과입니다. 원인을 단정하거나 0%로 해석하지 않습니다.')
    if len(rows) > 10:
        lines.append(f'표시만 첫 10행으로 제한했습니다. 저장 수집 행: {len(rows)}.')
    if not result.get('result_complete', False):
        lines.append(f'수집 제한으로 불완전한 결과입니다. 최대 {settings.max_rows}행 / {settings.max_bytes}바이트. 전체 건수는 알 수 없습니다.')
    lines.append(f'읽기 전용 · 실행 제한 {settings.query_timeout_ms / 1000:g}초 · 데이터 버전 {execution["data_version"]} · /sql로 SQL 확인, /evidence로 보고 연결')
    return '\n'.join(lines)
