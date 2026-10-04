"""Eight quality metrics; missing observations never become successes or zeros."""
import csv
import datetime as dt
import io
import json
import math
import statistics
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo
from fastapi import APIRouter
from fastapi.responses import Response
from . import assessments
from .errors import DomainError
from .store import now

VERSION = 'quality-metrics-v2'
FILTER_FIELDS = ('domain', 'requested_difficulty', 'difficulty', 'task_kind', 'model_version', 'rules_version')


def error_code(value):
    error = value.get('error')
    code = error.get('code') if isinstance(error, dict) else None
    safe = {'unknown', 'review_format', 'answer_exposure', 'format_invalid', 'provider_failure', 'timeout',
            'cancelled', 'server_restart', 'validation_failed', 'storage_failure', 'collection_failure', 'limit_reached'}
    if code in safe:
        return code
    if code == 'api_timeout':
        return 'timeout'
    if code in {'reauthorization_required', 'plan_permission_denied', 'usage_limit_exceeded', 'api_content_blocked',
                'api_key_missing', 'api_key_invalid', 'api_permission_denied', 'api_quota_exceeded',
                'api_rate_limited', 'api_unavailable', 'model_unavailable', 'response_incomplete'}:
        return 'provider_failure'
    return 'unknown'


def ratio(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator, 'rate': numerator / denominator if denominator else None,
            'measurement': 'measured' if denominator else 'unmeasured'}


def distribution(values):
    present = sorted(value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool))
    return {'count': len(present), 'missing': len(values) - len(present),
            'median_ms': statistics.median(present) if present else None,
            'p95_ms': present[math.ceil(.95 * len(present)) - 1] if present else None}


def korea_range(start_date=None, end_date=None):
    zone = ZoneInfo('Asia/Seoul')
    def boundary(value, add_day=False):
        if not value:
            return None
        try:
            date = dt.date.fromisoformat(value) + dt.timedelta(days=1 if add_day else 0)
        except ValueError as exc:
            raise DomainError('invalid_period', '기간은 YYYY-MM-DD 형식입니다.', 422) from exc
        return dt.datetime.combine(date, dt.time(), zone).astimezone(dt.timezone.utc)
    start, end = boundary(start_date), boundary(end_date, True)
    if start and end and start >= end:
        raise DomainError('invalid_period', '시작일은 종료일 이전이어야 합니다.', 422)
    return start, end


def matches(value, filters, anchor):
    for key in FILTER_FIELDS:
        if filters.get(key) and value.get(key) != filters[key]:
            return False
    start, end = korea_range(filters.get('start_date'), filters.get('end_date'))
    if start or end:
        stamp = value.get(anchor)
        if not stamp:
            return False
        stamp = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if stamp.tzinfo is None or start and stamp < start or end and stamp >= end:
            return False
    return True


def adapt_v1(event):
    """Read legacy metadata only. No inferred endings, verdicts, or versions."""
    allowed = {'event_id', 'event_type', 'occurred_at', 'operation_id', 'parent_operation_id', 'request_id',
               'attempt_id', 'status', 'duration_ms', 'error_code', 'rules_version', 'difficulty', 'task_kind'}
    value = {key: event[key] for key in allowed if key in event}
    value['schema_version'] = 'event-v1'
    if event.get('model'):
        value['model_version'] = event['model']
    usage = event.get('usage')
    if isinstance(usage, dict):
        for key in ('input_tokens', 'output_tokens'):
            if type(usage.get(key)) is int:
                value[key] = usage[key]
    return value


def adapt_quality_run(run):
    """Read only score/status metadata from legacy repeat samples."""
    records = []
    for sample in run.get('samples', []):
        for result in sample.get('results', []):
            value = {'sample_id': f"{run['run_id']}:{sample['id']}", 'repetition': result.get('repetition'),
                     'status': result.get('status'), 'created_at': run.get('started_at'),
                     'rules_version': run.get('rules_version'), 'model_version': result.get('model') or run.get('configured_model'),
                     'feedback': {'total_score': (result.get('feedback') or {}).get('total_score')}}
            if result.get('error'):
                value['error'] = {'code': error_code(result)}
            records.append(value)
    return records


def aggregate(events=(), verdicts=(), requests=(), reviews=(), pilots=(), operations=(), filters=None):
    filters = filters or {}
    # Idempotence across export input duplicates.
    events = list({e.get('event_id', f'no-id-{i}'): e for i, e in enumerate(events)}.values())
    starts = {e.get('operation_id'): e for e in events if e.get('event_type') == 'ai_started'}
    request_starts = {}
    for event in events:
        if event.get('event_type') == 'generation_started' and event.get('request_id'):
            key = event['request_id']
            if key not in request_starts or event.get('occurred_at', '') < request_starts[key].get('occurred_at', ''):
                request_starts[key] = event
    eligible_requests = []
    unsupported = 0
    for request in {r['request_id']: r for r in requests}.values():
        if request.get('status') == 'unsupported':
            unsupported += 1
            continue
        first = request_starts.get(request['request_id'])
        if first is None:
            # A persisted start is acceptable; interpretation timestamps are not.
            stamp = request.get('generation_started_at') or request.get('first_attempt_at')
            if not stamp:
                continue
            first = dict(request, occurred_at=stamp)
        merged = dict(request, first_attempt_at=first['occurred_at'])
        if matches(merged, filters, 'first_attempt_at'):
            eligible_requests.append(merged)
    latest = {}
    for value in verdicts:
        key = value['assessment_id']
        if key not in latest or value['revision'] > latest[key]['revision']:
            latest[key] = value
    # Several edits or reviewers must not inflate a task/participant denominator.
    targets = {}
    for value in latest.values():
        key = (value['target_kind'], value['target_id'], value.get('paired_target_id'), value.get('source', 'human'), value.get('criteria_version'), value.get('sample_id'), value.get('repetition'), value['target_kind'] == 'report_pair' and value['result'] == 'needs_improvement')
        if key not in targets or value.get('recorded_at', '') >= targets[key].get('recorded_at', ''):
            targets[key] = value
    verdicts = [a for a in targets.values() if matches(a, filters, 'target_occurred_at')]
    human = [a for a in verdicts if a.get('source', 'human') == 'human' and a['result'] != 'pending']
    by_kind = lambda kind: [a for a in human if a['target_kind'] == kind]
    pairs = by_kind('task_pair')
    natural_pairs = [a for a in pairs if a['result'] != 'intentional_repeat']
    difficulty = by_kind('difficulty')
    learner = [a for a in verdicts if a['target_kind'] == 'difficulty' and a.get('source') == 'learner']
    coaching = by_kind('coaching')
    review_verdicts = by_kind('review')
    selected_reviews = [r for r in reviews if matches(r, filters, 'created_at')]
    terminal_reviews = [r for r in selected_reviews if r.get('status') in {'completed', 'failed', 'cancelled', 'interrupted'}]
    repeat = defaultdict(list)
    for review in terminal_reviews:
        if review.get('sample_id'):
            repeat[review['sample_id']].append(review)
    repeated = []
    for sample, rows in repeat.items():
        scores = [r['feedback']['total_score'] for r in rows if r.get('status') == 'completed' and isinstance(r.get('feedback'), dict) and isinstance(r['feedback'].get('total_score'), (int, float))]
        repeated.append({'sample_id': sample, 'valid_count': len(scores), 'score_range': max(scores) - min(scores) if scores else None,
                         'failed_repetitions': sum(r.get('status') != 'completed' for r in rows)})
    selected_pilots = [p for p in pilots if matches(p, filters, 'started_at')]
    independent = sum(p.get('completed') is True and p.get('assistance') == 'no' for p in selected_pilots)
    report_pairs = by_kind('report_pair')
    initial = {a['target_id'] for a in report_pairs if a['result'] == 'needs_improvement'}
    improved = {a['target_id'] for a in report_pairs if a['result'] == 'improved'}
    submitted = {a['target_id'] for a in report_pairs if a['result'] in {'improved', 'not_improved'}}
    finished_ai = [e for e in events if e.get('event_type') == 'ai_finished' and matches(dict(e, started_at=starts.get(e.get('operation_id'), {}).get('occurred_at')), filters, 'started_at')]
    selected_ai_starts = [e for e in starts.values() if matches(e, filters, 'occurred_at')]
    ops = [o for o in operations if matches(o, filters, 'started_at')]
    terminal_ops = [o for o in ops if o.get('status') != 'running']
    request_durations = []
    for request in eligible_requests:
        if request.get('status') not in {'ready', 'failed', 'cancelled', 'interrupted'}:
            continue
        ended = request.get('finished_at') or request.get('completed_at')
        if ended:
            elapsed = (dt.datetime.fromisoformat(ended.replace('Z', '+00:00')) - dt.datetime.fromisoformat(request['first_attempt_at'].replace('Z', '+00:00'))).total_seconds() * 1000
            request_durations.append(max(0, int(elapsed)))
        else:
            request_durations.append(None)
    usage_groups = defaultdict(lambda: {'attempts': 0, 'retries': 0, 'observed_tokens': 0, 'missing_calls': 0})
    ends = {e.get('operation_id'): e for e in finished_ai}
    for start in selected_ai_starts:
        group = usage_groups[start.get('attempt_id') or start.get('request_id') or 'unlinked']
        group['attempts'] += 1
        group['retries'] += bool(start.get('parent_operation_id'))
        end = ends.get(start.get('operation_id'), {})
        if end.get('input_tokens') is None or end.get('output_tokens') is None:
            group['missing_calls'] += 1
        group['observed_tokens'] += sum(end.get(k) or 0 for k in ('input_tokens', 'output_tokens'))
    return {'metrics_version': VERSION, 'generated_at': now(), 'filters': filters,
            'generation_success': dict(ratio(sum(r.get('status') == 'ready' for r in eligible_requests), len(eligible_requests)), statuses=dict(Counter(r.get('status', 'unknown') for r in eligible_requests)), unsupported=unsupported),
            'diversity': dict(ratio(sum(a['result'] == 'meaningful_difference' for a in natural_pairs), len(natural_pairs)), intentional_repeat=sum(a['result'] == 'intentional_repeat' for a in pairs), pending=sum(a['target_kind'] == 'task_pair' and a['result'] == 'pending' for a in verdicts)),
            'difficulty_fit': dict(ratio(sum(a['result'] == 'appropriate' for a in difficulty), len(difficulty)), human_results=dict(Counter(a['result'] for a in difficulty)), learner_results=dict(Counter(a['result'] for a in learner)), unanswered=sum(a['result'] == 'pending' for a in learner)),
            'coaching_appropriateness': dict(ratio(sum(a['result'] == 'helpful' for a in coaching), len(coaching)), results=dict(Counter(a['result'] for a in coaching))),
            'evaluation_reliability': dict(ratio(sum(r.get('status') == 'completed' for r in terminal_reviews), len(terminal_reviews)), errors=dict(Counter(error_code(r) for r in terminal_reviews if r.get('status') != 'completed')), human_sample_count=len(review_verdicts), human_results=dict(Counter(a['result'] for a in review_verdicts)), repeated_samples=repeated),
            'independent_performance': dict(ratio(independent, len(selected_pilots)), interrupted=sum(bool(p.get('stopped_at')) for p in selected_pilots), ongoing=sum(not p.get('completed') and not p.get('stopped_at') for p in selected_pilots), assistance_unknown=sum(p.get('assistance') not in {'yes', 'no'} for p in selected_pilots)),
            'revision_effect': dict(ratio(len(initial & improved), len(initial)), unsubmitted=len(initial - submitted), unassessed=sum(a['target_kind'] == 'report_pair' and a['result'] == 'pending' for a in verdicts)),
            'wait_usage': {'duration': distribution([o.get('duration_ms') for o in terminal_ops]), 'request_duration': distribution(request_durations), 'statuses': dict(Counter(o.get('status') for o in ops)), 'ai_attempts': len(selected_ai_starts), 'ai_retries': sum(bool(e.get('parent_operation_id')) for e in selected_ai_starts), 'groups': dict(usage_groups)},
            'versions': {'event_schema': dict(Counter(e.get('schema_version', 'unknown') for e in events)), 'rules': dict(Counter(e.get('rules_version', 'unknown') for e in events)), 'criteria': dict(Counter(a.get('criteria_version', 'unknown') for a in verdicts))}}


def collect(store, filters=None):
    with store.connect() as conn:
        rows = lambda table: [r['payload'] for r in conn.execute(f'SELECT payload FROM {table}')]
        contexts = {row['attempt_id']: row for row in rows('attempts')}
        def context(values):
            return [dict(contexts.get(value.get('attempt_id') or (value.get('attempt_ids') or [None])[0], {}), **value) for value in values]
        events = context(rows('quality_events_v2') + [adapt_v1(e) for e in rows('training_events')])
        verdicts = context(assessments.latest(conn))
        reviews = context(rows('reviews')) + [review for run in rows('quality_runs') for review in adapt_quality_run(run)]
        result = aggregate(events=events, verdicts=verdicts, requests=context(rows('training_requests')), reviews=reviews,
                           pilots=context(rows('pilot_records')), operations=context(rows('quality_operations')), filters=filters)
        # Split semantic verdicts and review reliability by applied version.
        versions = sorted({a.get('criteria_version', 'unknown') for a in verdicts})
        result['assessment_version_groups'] = {}
        for version in versions:
            group = aggregate(verdicts=[a for a in verdicts if a.get('criteria_version', 'unknown') == version], filters=filters)
            result['assessment_version_groups'][version] = {key: group[key] for key in ('diversity', 'difficulty_fit', 'coaching_appropriateness', 'revision_effect')}
        result['review_version_groups'] = {}
        for version in sorted({r.get('rules_version', 'unknown') for r in reviews}):
            group = aggregate(reviews=[r for r in reviews if r.get('rules_version', 'unknown') == version], filters=filters)
            result['review_version_groups'][version] = group['evaluation_reliability']
        health = conn.execute('SELECT payload FROM quality_collection_health WHERE id=1').fetchone()['payload']
    start, _ = korea_range((filters or {}).get('start_date'), (filters or {}).get('end_date'))
    result['collection'] = dict(health, outside_retention=bool(start and start < dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=health.get('retention_days', 30))))
    return result


def safe_csv(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')) else text


def export_csv(value):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['metric', 'field', 'value', 'metrics_version'])
    for key, metric in value.items():
        if isinstance(metric, dict):
            for field, content in metric.items():
                text = json.dumps(content, ensure_ascii=False) if isinstance(content, (list, dict)) else '' if content is None else content
                writer.writerow([safe_csv(key), safe_csv(field), safe_csv(text), VERSION])
    return stream.getvalue()


def routes(app, store):
    router = APIRouter()

    @router.get('/api/quality/metrics')
    def read_metrics(start_date: str | None = None, end_date: str | None = None, domain: str | None = None,
                     requested_difficulty: str | None = None, difficulty: str | None = None, task_kind: str | None = None,
                     model_version: str | None = None, rules_version: str | None = None):
        values = locals()
        return collect(store, {k: values[k] for k in ('start_date', 'end_date', *FILTER_FIELDS) if values[k] is not None})

    @router.get('/api/quality/metrics/export')
    def export_metrics(format: str = 'json', start_date: str | None = None, end_date: str | None = None,
                       domain: str | None = None, requested_difficulty: str | None = None, difficulty: str | None = None,
                       task_kind: str | None = None, model_version: str | None = None, rules_version: str | None = None):
        values = locals()
        filters = {k: values[k] for k in ('start_date', 'end_date', *FILTER_FIELDS) if values[k] is not None}
        if format not in {'json', 'csv'}:
            raise DomainError('invalid_format', 'json 또는 csv를 선택하세요.', 422)
        value = collect(store, filters)
        content = json.dumps(value, ensure_ascii=False) if format == 'json' else export_csv(value)
        return Response(content, media_type='application/json' if format == 'json' else 'text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="quality-metrics-v2.{format}"'})

    app.include_router(router)
