"""Finite public-data semantic compiler for Discord, independent of the web app.

The provider interprets conditions, never supplies executable SQL or numbers.
The caller owns authorization, persistence and per-user call budgets.
"""
import copy
import datetime as dt
import json
import re

from .sql_runner import check_query


SCHEMA = {
    'users': {'user_id', 'signup_date', 'channel'},
    'tutorial_attempts': {'user_id', 'step', 'completed', 'attempt_at'},
    'sessions': {'user_id', 'session_at'},
}
METRICS = {'users_count', 'attempts_count', 'duplicate_attempts', 'tutorial_rate',
           'd7_retention', 'weekly_return'}
KEYS = {'metric', 'start', 'end', 'timezone', 'unit', 'numerator', 'denominator',
        'group_by', 'filters', 'step', 'event_start', 'event_end',
        'baseline_start', 'baseline_end', 'period_basis'}

UNSUPPORTED_MESSAGE = ('현재 조회는 가입자 수, 도전 이벤트 수, 단계별 중복 점검, 튜토리얼 완료율, D7·주간 재방문율을 지원합니다. '
                       '집계는 전체·유입 채널별·가입일별로 가능합니다. 유저별·단계별 최초 완료까지의 도전 횟수는 아직 지원하지 않습니다. '
                       '대안으로 특정 단계의 전체 도전 수나 중복 점검을 요청할 수 있습니다.')


def missing_condition_question(conditions):
    """Ask one relevant question from finite public keys; never echo model prose."""
    c = conditions if isinstance(conditions, dict) else {}
    if c.get('metric') not in METRICS:
        return '어떤 지표를 조회할까요? 가입자 수·도전 수·완료율·재방문율 중 요청할 지표를 알려주세요.'
    if not c.get('start') or not c.get('end'):
        return '조회 기간의 시작일과 종료일을 알려주세요. 과제에 적힌 기간을 사용하려면 “과제 기간”이라고 답해주세요. 종료일은 제외합니다.'
    if not c.get('timezone'):
        return '시간대는 과제에 공개된 기준을 사용할까요? 사용할 시간대를 알려주세요.'
    if not c.get('unit'):
        return '계산 단위는 고유 사용자 수인가요, 도전 이벤트 수인가요?'
    if c['metric'] in {'tutorial_rate', 'duplicate_attempts'} and c.get('step') is None:
        return '튜토리얼 몇 단계의 결과를 조회할까요?'
    if c['metric'] in {'tutorial_rate', 'd7_retention', 'weekly_return'}:
        if not c.get('denominator'):
            return '비율의 분모는 누구인가요? 예를 들어 신규 가입자 전체인지, 해당 단계를 도전한 사용자만인지 알려주세요.'
        if not c.get('numerator'):
            return '분모 중 어떤 조건을 충족한 사용자를 분자로 셀까요?'
    if c['metric'] == 'tutorial_rate' and (not c.get('event_start') or not c.get('event_end')):
        return '튜토리얼 완료 이벤트는 언제부터 언제까지 관측할까요? 가입 기간과 구분해 알려주세요. 과제의 관측 종료일까지 사용할 수도 있습니다.'
    if c['metric'] == 'weekly_return' and (not c.get('baseline_start') or not c.get('baseline_end')):
        return '재방문 비교의 기준 기간 시작일과 종료일을 알려주세요.'
    return '정리한 조건으로 조회하려는 것이 맞나요? 맞으면 확인이라고 답하거나 변경할 조건을 알려주세요.'


class QueryContractError(ValueError):
    pass


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise QueryContractError('날짜는 YYYY-MM-DD로 지정하세요.')
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise QueryContractError('유효한 날짜를 지정하세요.') from None
    return value


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _public(task):
    """Strict projection: arbitrary dictionary values may contain private data."""
    schema = task.get('schema', {})
    if not isinstance(schema, dict):
        raise QueryContractError('공개 스키마가 없습니다.')
    safe_schema = {}
    for table, required in SCHEMA.items():
        columns = schema.get(table, {})
        if isinstance(columns, dict):
            # Only actual type names cross the model boundary, never arbitrary
            # nested descriptions or unexpected schema payloads.
            safe_schema[table] = {name: columns[name].lower() for name in required
                                  if isinstance(columns.get(name), str) and columns[name].lower()
                                  in {'text', 'date', 'integer', 'int', 'boolean', 'bool', 'timestamptz', 'timestamp with time zone'}}
    public = {'schema': safe_schema, 'period': {k: task.get('period', {}).get(k)
            for k in ('start', 'end', 'observation_end')}, 'timezone': task.get('timezone'),
            'supported_metrics': sorted(METRICS)}
    if isinstance(task.get('objective'), str):
        public['objective'] = task['objective'][:2000]
    public['dictionary'] = {}
    for table in safe_schema:
        dictionary = task.get('dictionary', {}).get(table, {})
        if isinstance(dictionary, dict):
            public['dictionary'][table] = {k: dictionary[k][:2000] for k in ('description', 'unit')
                                            if isinstance(dictionary.get(k), str)}
            public['dictionary'][table]['columns'] = safe_schema[table]
    public['metrics'] = {}
    for metric in METRICS:
        definition = task.get('metrics', {}).get(metric, {})
        if isinstance(definition, dict):
            public['metrics'][metric] = {k: definition[k] for k in
                ('numerator', 'denominator', 'step', 'event_start', 'event_end', 'observation_end')
                if type(definition.get(k)) in (str, int)}
    public['accepted_limits'] = [value[:2000] for value in task.get('accepted_limits', []) if isinstance(value, str)]
    return public


def _validate(conditions, task):
    if not isinstance(conditions, dict) or set(conditions) - KEYS:
        raise QueryContractError('지원하지 않는 조회 조건입니다.')
    c = copy.deepcopy(conditions)
    if c.get('metric') not in METRICS:
        raise QueryContractError('이 과제에서 지원하지 않는 지표입니다.')
    for key in ('start', 'end'):
        if key not in c:
            raise QueryContractError('조회 시작·종료 기간을 정의해주세요.')
        _date(c[key])
    if c['start'] >= c['end']:
        raise QueryContractError('종료 날짜는 시작 날짜보다 뒤여야 합니다.')
    if c.get('timezone') != task.get('timezone') or c.get('timezone') not in {'UTC', 'Asia/Seoul'}:
        raise QueryContractError('과제에 공개된 시간대를 지정해주세요.')
    if c.get('period_basis') != 'explicit_dates':
        raise QueryContractError('달력 주인지 최근 7일인지 확인하여 실제 날짜를 지정해주세요.')
    if c.get('group_by') not in (None, 'channel', 'signup_date'):
        raise QueryContractError('공개 그룹 기준은 channel 또는 signup_date입니다.')
    if not isinstance(c.get('filters', {}), dict) or set(c.get('filters', {})) - {'channel'}:
        raise QueryContractError('공개 필터는 channel만 지원합니다.')
    channel = c.get('filters', {}).get('channel')
    if channel is not None and (not isinstance(channel, str) or len(channel) > 100):
        raise QueryContractError('유입 채널 필터를 확인해주세요.')
    c.setdefault('filters', {})
    c.setdefault('group_by', None)
    metric = c['metric']
    needed = {'users'}
    if metric in {'attempts_count', 'duplicate_attempts', 'tutorial_rate'}:
        needed.add('tutorial_attempts')
        if c.get('step') is not None and (type(c['step']) is not int or not 1 <= c['step'] <= 100):
            raise QueryContractError('튜토리얼 단계는 1~100 정수로 지정하세요.')
        if metric in {'tutorial_rate', 'duplicate_attempts'} and c.get('step') is None:
            raise QueryContractError('조회할 튜토리얼 단계를 정의해주세요.')
    if metric in {'d7_retention', 'weekly_return'}:
        needed.add('sessions')
    safe = _public(task)
    if any(not SCHEMA[t].issubset(safe['schema'].get(t, {})) for t in needed):
        raise QueryContractError('요청에 필요한 공개 테이블·컬럼이 없습니다.')
    if safe['schema']['users']['signup_date'] != 'date':
        raise QueryContractError('이 조회 계약은 가입 날짜 DATE 형식만 지원합니다.')
    for table, column in [('tutorial_attempts', 'attempt_at'), ('sessions', 'session_at')]:
        if table in needed and safe['schema'][table][column] not in {'timestamptz', 'timestamp with time zone'}:
            raise QueryContractError('이 조회 계약은 시간대가 있는 이벤트 시각만 지원합니다.')
    expected_unit = 'attempt' if metric in {'attempts_count', 'duplicate_attempts'} else 'user'
    if c.get('unit') != expected_unit:
        raise QueryContractError('고유 사용자와 이벤트 수의 계산 단위를 정의해주세요.')
    if metric in {'tutorial_rate', 'd7_retention', 'weekly_return'}:
        denominators = {'tutorial_rate': {'signup_users', 'attempted_users'},
                        'd7_retention': {'observed_signup_users'}, 'weekly_return': {'baseline_active_users'}}
        numerators = {'tutorial_rate': 'completed_users', 'd7_retention': 'd7_active_users',
                      'weekly_return': 'returning_users'}
        if c.get('denominator') not in denominators[metric] or c.get('numerator') != numerators[metric]:
            raise QueryContractError('비율의 분자·분모를 정의해주세요.')
    if metric == 'tutorial_rate':
        for key in ('event_start', 'event_end'):
            _date(c.get(key))
        if c['event_start'] >= c['event_end']:
            raise QueryContractError('완료 이벤트 관측 기간을 확인해주세요.')
    if metric == 'weekly_return':
        for key in ('baseline_start', 'baseline_end'):
            _date(c.get(key))
        if not c['baseline_start'] < c['baseline_end'] <= c['start']:
            raise QueryContractError('재방문 기준 기간은 비교 기간보다 앞서야 합니다.')
    if metric == 'd7_retention':
        _date(safe['period'].get('observation_end'))
    observation_end = safe['period'].get('observation_end')
    if observation_end:
        _date(observation_end)
        if max(c['end'], c.get('event_end', c['end'])) > observation_end:
            raise QueryContractError('공개된 관측 종료 이후의 기간은 조회할 수 없습니다.')
    return c, needed


def compile_query(conditions, public_task):
    """Compile only validated semantic conditions; model SQL is never consumed."""
    c, tables = _validate(conditions, public_task)
    tz = _literal(c['timezone'])
    start, end = _literal(c['start']), _literal(c['end'])
    # Representative contract: signup_date DATE, event timestamps TIMESTAMPTZ.
    def event_date(column):
        return f'CAST({column} AT TIME ZONE {tz} AS DATE)'
    def window(column, a=start, b=end):
        return f'{column} >= DATE {a} AND {column} < DATE {b}'
    filt = '' if not c['filters'].get('channel') else ' AND u.channel = ' + _literal(c['filters']['channel'])
    group = 'u.' + c['group_by'] if c['group_by'] else None
    prefix = f'{group} AS group_value, ' if group else ''
    suffix = f' GROUP BY {group} ORDER BY {group}' if group else ''
    metric = c['metric']
    if metric == 'users_count':
        query = f'SELECT {prefix}COUNT(DISTINCT u.user_id) AS user_count FROM users u WHERE {window("u.signup_date")}{filt}{suffix}'
    elif metric in {'attempts_count', 'duplicate_attempts'}:
        step = '' if c.get('step') is None else f' AND a.step = {c["step"]}'
        value = 'COUNT(*)' if metric == 'attempts_count' else 'COUNT(*) - COUNT(DISTINCT a.user_id)'
        query = f'SELECT {prefix}{value} AS {metric} FROM tutorial_attempts a JOIN users u ON u.user_id=a.user_id WHERE {window(event_date("a.attempt_at"))}{step}{filt}{suffix}'
    else:
        if metric == 'tutorial_rate':
            event_window = window(event_date('a.attempt_at'), _literal(c['event_start']), _literal(c['event_end']))
            attempt = f'EXISTS (SELECT 1 FROM tutorial_attempts a WHERE a.user_id=u.user_id AND a.step={c["step"]} AND {event_window})'
            completed = f'EXISTS (SELECT 1 FROM tutorial_attempts a WHERE a.user_id=u.user_id AND a.step={c["step"]} AND a.completed IS TRUE AND {event_window})'
            eligibility = window('u.signup_date')
            if c['denominator'] == 'attempted_users':
                eligibility += ' AND ' + attempt
        elif metric == 'd7_retention':
            eligibility = window('u.signup_date') + f' AND u.signup_date + 8 <= DATE {_literal(public_task["period"]["observation_end"])}'
            completed = f'EXISTS (SELECT 1 FROM sessions s WHERE s.user_id=u.user_id AND {event_date("s.session_at")} = u.signup_date + 7)'
        else:
            eligibility = f'EXISTS (SELECT 1 FROM sessions s WHERE s.user_id=u.user_id AND {window(event_date("s.session_at"), _literal(c["baseline_start"]), _literal(c["baseline_end"]))})'
            completed = f'EXISTS (SELECT 1 FROM sessions s WHERE s.user_id=u.user_id AND {window(event_date("s.session_at"))})'
        # EXISTS avoids multiplying users by their attempts/sessions.
        query = (f'SELECT {prefix}COUNT(DISTINCT u.user_id) AS denominator, '
                 f'COUNT(DISTINCT CASE WHEN {completed} THEN u.user_id END) AS numerator, '
                 f'100.0 * COUNT(DISTINCT CASE WHEN {completed} THEN u.user_id END) / '
                 f'NULLIF(COUNT(DISTINCT u.user_id),0) AS rate_percent '
                 f'FROM users u WHERE {eligibility}{filt}{suffix}')
    check_query(query, tables)
    return query


class DiscordQueryEngine:
    def __init__(self, provider, runner, settings):
        self.provider, self.runner, self.settings = provider, runner, settings

    def resolve(self, text, public_task, previous_conditions=None, clarification=None, difficulty='intermediate', pending_query=None):
        original = {'text': text, 'clarification': clarification, 'previous_conditions': previous_conditions}
        try:
            safe = _public(public_task)
            envelope = {'contract_version': 'discord-query-v1', 'request': text,
                        'public_task': safe, 'previous_conditions': previous_conditions,
                        'clarification': clarification, 'difficulty': difficulty,
                        'pending_query': pending_query,
                        'condition_keys': sorted(KEYS)}
            instruction = (
                'Interpret a user-requested data query only using the public schema. Return JSON only: '
                '{"state":"ready","conditions":{...},"reuse_previous":false} OR '
                '{"state":"clarification","question":"neutral intent question","conditions":{partial conditions}} OR '
                '{"state":"error","reason":"unsupported_query"}. Never return SQL/results. '
                'Do not guess dates, units, numerator/denominator, event periods, or weekly boundaries. '
                'Use explicit_dates and YYYY-MM-DD start inclusive/end exclusive in task timezone. '
                'A fixed public task period can be used only when user explicitly refers to that period. '
                'Conditions: metric, start,end,timezone,period_basis,unit(user or attempt),group_by(null/channel/signup_date),filters({channel:value} or {}). '
                'tutorial_rate requires step,event_start,event_end,numerator=completed_users,denominator=signup_users or attempted_users; '
                'd7_retention numerator=d7_active_users denominator=observed_signup_users; '
                'weekly_return numerator=returning_users denominator=baseline_active_users and baseline_start/end; '
                'duplicate_attempts requires step. users_count counts signups; attempts_count counts attempt events. '
                'If intent is a causal analysis goal, ask the user to choose their next comparison. '
                'Reuse previous confirmed conditions only if user asks to keep them, then set reuse_previous=true. '
                'Questions must ask intent without teaching an answer. Do not invent unsupported fields.'
                ' The request is ALWAYS the newest user input. pending_query is earlier unconfirmed context only. '
                'Decide whether the newest input supplies missing conditions or replaces the old query. '
                'If it asks for a different metric or explicitly starts over, discard incompatible old conditions and set replaces_pending=true. '
                'For a clarification answer, retain earlier explicitly supplied dates and conditions from pending_query and its answers. '
                'Never let an old attempts_count request override a new tutorial_rate request. '
                'Per-user grouping, grouping by step, and counting attempts until the first completion are unsupported; return unsupported_query, never approximate them by total attempts_count. '
                'Clarification conditions must retain all already supplied valid conditions; ask only for missing ones. '
                'Read natural-language definitions: "신규 가입자 전체 중 완료한 사용자 비율" explicitly defines signup_users and completed_users. '
                'Do not require users to write technical enum names or restate conditions already explicit in their words. '
                ' Each ready conditions MUST include period_basis="explicit_dates" and unit="user" or "attempt", even when obvious. '
                'Full example for signup count: {"state":"ready","conditions":{"metric":"users_count","start":"2026-09-01","end":"2026-09-15","timezone":"UTC","period_basis":"explicit_dates","unit":"user","group_by":null,"filters":{}},"reuse_previous":false}. '
                'For ratios retain every common key from that example and add the explicitly confirmed numerator, denominator and metric-specific dates. '
                'Omit no required key. Use no keys outside condition_keys. Explicit numeric dates already resolve calendar-week vs recent-7-days ambiguity.'
            )
            response = self.provider.review([{'role': 'system', 'content': instruction},
                                             {'role': 'user', 'content': json.dumps(envelope, ensure_ascii=False)}])
            metadata = {k: response[k] for k in ('usage', 'model', 'provider_diagnostic') if k in response}
            if response.get('state') != 'completed':
                return dict(state='error', reason=response.get('reason', 'provider_unavailable'), original=original, **metadata)
            parsed = json.loads(response.get('text', ''))
            if not isinstance(parsed, dict):
                raise QueryContractError('조회 응답 형식을 확인할 수 없습니다.')
            conditions = copy.deepcopy(parsed.get('conditions', {}))
            if isinstance(conditions, dict):
                # An explicit public-period reference is a user choice, not an inferred date.
                references = [text]
                earlier = (pending_query or {}).get('proposed_conditions', {})
                old_metric = earlier.get('metric')
                if pending_query and parsed.get('replaces_pending') is not True and conditions.get('metric', old_metric) == old_metric:
                    conditions = {**earlier, **conditions}
                if pending_query and old_metric == conditions.get('metric') and parsed.get('replaces_pending') is not True:
                    references += [pending_query.get('request', '')] + pending_query.get('answers', [])
                if any(marker in reference for reference in references if isinstance(reference, str)
                       for marker in ('과제 기간', '공개 기간')):
                    for key in ('start', 'end'):
                        if key not in conditions and safe['period'].get(key):
                            conditions[key] = safe['period'][key]
                metadata['replaces_pending'] = (parsed.get('replaces_pending') is True or
                    old_metric in METRICS and conditions.get('metric') in METRICS and old_metric != conditions['metric'])
            if parsed.get('state') == 'clarification':
                return self._clarification(missing_condition_question(conditions), original, difficulty, metadata, conditions)
            if parsed.get('state') != 'ready':
                return dict(state='error', reason='unsupported_query', message=UNSUPPORTED_MESSAGE, original=original, **metadata)
            if isinstance(conditions, dict) and isinstance(conditions.get('filters'), dict) and 'step' in conditions['filters']:
                # Equivalent placement of the supported step condition only.
                # A conflict remains invalid and requires user confirmation.
                conditions = copy.deepcopy(conditions)
                step = conditions['filters'].get('step')
                if 'step' not in conditions or conditions['step'] == step:
                    conditions['step'] = conditions['filters'].pop('step')
            if parsed.get('reuse_previous') is True and previous_conditions:
                conditions = dict(previous_conditions, **conditions)
            try:
                sql = compile_query(conditions, public_task)
            except QueryContractError as exc:
                return self._clarification(str(exc), original, difficulty, metadata, conditions)
            return dict(state='ready', conditions=_validate(conditions, public_task)[0], sql=sql,
                        original=original, help_type='request_confirmation', **metadata)
        except QueryContractError as exc:
            return dict(state='error', reason='public_contract_invalid', message=str(exc), original=original)
        except (ValueError, TypeError, KeyError, AttributeError):
            return dict(state='error', reason='provider_invalid_response', original=original)
        except Exception:
            return dict(state='error', reason='provider_unavailable', original=original)

    @staticmethod
    def _clarification(question, original, difficulty, metadata, proposed_conditions=None):
        options = ['고유 사용자 수', '도전 이벤트 수', '분자·분모가 정의된 비율'] if difficulty == 'beginner' else []
        proposed = {key: copy.deepcopy(value) for key, value in proposed_conditions.items() if key in KEYS} if isinstance(proposed_conditions, dict) else {}
        return dict(state='clarification', question=question, options=options, original=original,
                    help_type='concept_hint' if options else 'request_confirmation',
                    proposed_conditions=proposed, **metadata)

    def execute(self, session_id, schema_name, plan, public_task):
        try:
            if plan.get('state') != 'ready':
                raise QueryContractError('조회 조건 확인을 먼저 완료해주세요.')
            conditions, tables = _validate(plan.get('conditions'), public_task)
            sql = compile_query(conditions, public_task)
            if plan.get('sql') != sql:
                raise QueryContractError('확정 조건과 SQL이 일치하지 않습니다.')
        except (QueryContractError, ValueError, TypeError, AttributeError) as exc:
            return {'state': 'error', 'reason': 'query_contract_invalid', 'message': str(exc), 'attempts': []}
        attempts = []
        for _ in range(2):
            try:
                result = self.runner.execute(session_id, schema_name, sql, allowed_tables=tables)
            except Exception:
                result = {'status': 'error', 'error': {'code': 'runner_unavailable', 'message': '조회 서비스를 사용할 수 없습니다.'}}
            attempts.append({'sql': sql, 'result': copy.deepcopy(result), 'error': copy.deepcopy(result.get('error'))})
            if result.get('status') == 'success':
                try:
                    saved = copy.deepcopy(self.runner.get(session_id, result['execution_id']))
                    if saved.get('sql') != sql or saved.get('result', {}).get('status') != 'success':
                        raise QueryContractError('전체 결과와 SQL이 일치하지 않습니다.')
                except Exception:
                    return {'state': 'error', 'reason': 'result_capture_failed', 'sql': sql, 'attempts': attempts, 'result': result}
                return {'state': 'success', 'sql': sql, 'conditions': conditions, 'attempts': attempts,
                        'result': result, 'saved_execution': saved, 'full_result': saved['result'],
                        'limits': {key: getattr(self.settings, key, None) for key in
                                   ('preview_rows', 'max_rows', 'max_bytes', 'query_timeout_ms')}}
            if (result.get('error') or {}).get('code') not in {'connection', '40001', '40P01'}:
                break
        return {'state': 'error', 'reason': 'query_failed', 'sql': sql, 'conditions': conditions,
                'attempts': attempts, 'result': result}
