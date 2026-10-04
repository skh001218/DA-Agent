"""Append-only, raw-free operational telemetry and durable operation state."""
import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from psycopg.types.json import Jsonb

from .errors import DomainError
from .store import now

EVENT_TYPES = {'request_state', 'generation_started', 'generation_finished', 'loading_started',
               'loading_finished', 'validation_started', 'validation_finished', 'task_published',
               'sql_started', 'sql_finished', 'coaching_started', 'coaching_finished', 'hint_used',
               'explanation_used', 'submission', 'review_started', 'review_finished',
               'recommendation_shown', 'recommendation_selected', 'assessment_changed',
               'pilot_started', 'pilot_finished', 'ai_started', 'ai_finished'}
ERROR_CODES = {'server_restart', 'provider_failure', 'timeout', 'cancelled', 'format_invalid',
               'validation_failed', 'storage_failure', 'collection_failure', 'limit_reached'}


class EventV2(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    event_id: str = Field(min_length=1, max_length=100)
    event_type: str
    schema_version: Literal['event-v2'] = 'event-v2'
    occurred_at: str
    operation_id: str = Field(min_length=1, max_length=100)
    parent_operation_id: str | None = None
    request_id: str | None = None
    attempt_id: str | None = None
    task_id: str | None = None
    package_id: str | None = None
    report_id: str | None = None
    review_id: str | None = None
    content_version: str | None = None
    difficulty_version: str | None = None
    evaluation_version: str | None = None
    rules_version: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    domain: str | None = None
    requested_difficulty: str | None = None
    difficulty: str | None = None
    task_kind: str | None = None
    status: Literal['running', 'success', 'failed', 'cancelled', 'interrupted', 'ready', 'unsupported', 'completed']
    error_code: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    row_count: int | None = Field(default=None, ge=0)
    complete: bool | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    usage_missing_reason: Literal['provider_unavailable', 'failed_call', 'not_reported'] | None = None

    @model_validator(mode='after')
    def contract(self):
        if self.event_type not in EVENT_TYPES or self.error_code and self.error_code not in ERROR_CODES:
            raise ValueError('unknown event type or error code')
        stamp = dt.datetime.fromisoformat(self.occurred_at.replace('Z', '+00:00'))
        if stamp.utcoffset() != dt.timedelta(0):
            raise ValueError('occurred_at must be UTC')
        if self.event_type.endswith('_started') and self.status != 'running':
            raise ValueError('start event must be running')
        if self.event_type.endswith('_finished') and self.status not in {'success', 'completed', 'failed', 'cancelled', 'interrupted', 'ready'}:
            raise ValueError('finish event must be terminal')
        if self.status == 'ready' and self.event_type not in {'generation_finished', 'request_state', 'task_published'}:
            raise ValueError('ready is only valid for task generation/publication')
        if self.status == 'unsupported' and self.event_type != 'request_state':
            raise ValueError('unsupported is only a request interpretation state')
        if (self.row_count is not None or self.complete is not None) and self.event_type != 'sql_finished':
            raise ValueError('SQL metadata belongs to SQL events only')
        if (self.input_tokens is not None or self.output_tokens is not None or self.usage_missing_reason) and self.event_type != 'ai_finished':
            raise ValueError('usage belongs to AI events only')
        if self.event_type.endswith('_started') and (self.error_code or self.duration_ms is not None):
            raise ValueError('start event cannot contain terminal failure/time metadata')
        if self.error_code and self.status not in {'failed', 'cancelled', 'interrupted'}:
            raise ValueError('error metadata needs a failure state')
        return self


def initialize(store, retention_days=30):
    with store.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS quality_events_v2 (event_id text PRIMARY KEY, payload jsonb NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS quality_operations (operation_id text PRIMARY KEY, payload jsonb NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS quality_collection_health (id integer PRIMARY KEY, payload jsonb NOT NULL)')
        conn.execute('INSERT INTO quality_collection_health VALUES(1,%s) ON CONFLICT DO NOTHING',
                     (Jsonb({'incomplete': False, 'retention_days': retention_days}),))
        conn.execute('UPDATE quality_collection_health SET payload=payload || %s WHERE id=1', (Jsonb({'retention_days': retention_days}),))
        for row in conn.execute("SELECT operation_id,payload FROM quality_operations WHERE payload->>'status'='running' FOR UPDATE"):
            value = row['payload']
            stamp = now()
            data = {key: val for key, val in value.items() if key in EventV2.model_fields}
            data.update(event_id='restart-' + str(uuid.uuid4()), event_type=f"{value['kind']}_finished",
                        occurred_at=stamp, status='interrupted', error_code='server_restart', duration_ms=None)
            if value['kind'] == 'ai':
                data['usage_missing_reason'] = 'failed_call'
            append(conn, data)
            value.update(status='interrupted', finished_at=stamp, error_code='server_restart', duration_ms=None)
            conn.execute('UPDATE quality_operations SET payload=%s WHERE operation_id=%s', (Jsonb(value), row['operation_id']))
        conn.execute("DELETE FROM quality_events_v2 WHERE (payload->>'occurred_at')::timestamptz < now() - %s * interval '1 day'", (retention_days,))


def append(conn, event):
    event = event if isinstance(event, EventV2) else EventV2.model_validate(event)
    payload = event.model_dump(exclude_none=True)
    inserted = conn.execute('INSERT INTO quality_events_v2 VALUES(%s,%s) ON CONFLICT DO NOTHING RETURNING payload',
                            (event.event_id, Jsonb(payload))).fetchone()
    if inserted:
        return payload
    existing = conn.execute('SELECT payload FROM quality_events_v2 WHERE event_id=%s', (event.event_id,)).fetchone()['payload']
    if existing != payload:
        raise DomainError('idempotency_conflict', '이벤트 ID가 다른 본문에 사용되었습니다.', 409)
    return existing


def begin(store, kind, operation_id=None, parent_operation_id=None, call_limit=None, **metadata):
    """Reserve durable state and (for AI) call budget before executing work."""
    operation_id = operation_id or str(uuid.uuid4())
    with store.connect() as conn:
        if call_limit is not None:
            scope = metadata.get('attempt_id') or metadata.get('request_id')
            if not scope:
                raise DomainError('invalid_scope', 'AI 호출 한도에 훈련 또는 요청 ID가 필요합니다.', 422)
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (scope,))
            used = conn.execute("SELECT count(*) AS n FROM quality_operations WHERE payload->>'kind'='ai' AND (payload->>'attempt_id'=%s OR payload->>'request_id'=%s)", (scope, scope)).fetchone()['n']
            if used >= call_limit:
                raise DomainError('limit_reached', 'AI 호출 한도를 모두 사용했습니다.', 429)
        stamp = now()
        event = EventV2(event_id=str(uuid.uuid4()), event_type=f'{kind}_started', operation_id=operation_id,
                        parent_operation_id=parent_operation_id, occurred_at=stamp, status='running', **metadata)
        payload = dict(event.model_dump(exclude_none=True), kind=kind, started_at=stamp)
        if parent_operation_id and not conn.execute('SELECT 1 FROM quality_operations WHERE operation_id=%s', (parent_operation_id,)).fetchone():
            raise DomainError('invalid_parent', '재시도할 원 작업을 찾을 수 없습니다.', 422)
        inserted = conn.execute('INSERT INTO quality_operations VALUES(%s,%s) ON CONFLICT DO NOTHING RETURNING operation_id', (operation_id, Jsonb(payload))).fetchone()
        if not inserted:
            raise DomainError('operation_conflict', '이미 사용한 작업 ID입니다.', 409)
        append(conn, event)
    return operation_id


def finish(store, operation_id, status, error_code=None, **metadata):
    with store.connect() as conn:
        row = conn.execute('SELECT payload FROM quality_operations WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()
        if not row:
            raise DomainError('not_found', '작업을 찾을 수 없습니다.', 404)
        value = row['payload']
        if value['status'] != 'running':
            raise DomainError('terminal_operation', '종료된 작업의 상태는 변경할 수 없습니다.', 409)
        stamp = now()
        duration = max(0, int((dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')) - dt.datetime.fromisoformat(value['started_at'].replace('Z', '+00:00'))).total_seconds() * 1000))
        data = {key: val for key, val in value.items() if key in EventV2.model_fields}
        data.update(event_id=str(uuid.uuid4()), event_type=f"{value['kind']}_finished", occurred_at=stamp,
                    status=status, error_code=error_code, duration_ms=duration, **metadata)
        event = EventV2.model_validate(data)
        append(conn, event)
        value.update(status=status, finished_at=stamp, duration_ms=duration, error_code=error_code, **metadata)
        conn.execute('UPDATE quality_operations SET payload=%s WHERE operation_id=%s', (Jsonb(value), operation_id))
    return value


def record_optional(store, event):
    """Collection failure must not roll back a saved learner input."""
    try:
        with store.connect() as conn:
            return append(conn, event)
    except Exception:
        try:
            with store.connect() as conn:
                conn.execute('UPDATE quality_collection_health SET payload=payload || %s WHERE id=1',
                             (Jsonb({'incomplete': True, 'error_code': 'collection_failure', 'since': now()}),))
        except Exception:
            # The caller also receives a failure flag during a full DB outage.
            pass
        return {'collected': False, 'incomplete': True, 'error_code': 'collection_failure'}


def delete_for_attempt(conn, attempt_id):
    for table in ('quality_events_v2', 'quality_operations'):
        conn.execute(f"DELETE FROM {table} WHERE payload->>'attempt_id'=%s OR payload->>'request_id' IN (SELECT request_id FROM training_requests WHERE attempt_id=%s)", (attempt_id, attempt_id))


def routes(app, store):
    router = APIRouter()

    @router.post('/api/quality/events')
    def create_event(event: EventV2):
        with store.connect() as conn:
            return append(conn, event)

    app.include_router(router)
