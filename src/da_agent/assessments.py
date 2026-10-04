"""Versioned human/learner verdicts with validated stored targets."""
import uuid
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from psycopg.types.json import Jsonb
from .errors import DomainError
from .store import now

RESULTS = {
    'task_pair': {'meaningful_difference', 'no_difference', 'intentional_repeat', 'pending'},
    'difficulty': {'appropriate', 'ambiguous', 'unanalyzable', 'too_easy', 'too_hard', 'pending'},
    'coaching': {'helpful', 'disruptive', 'repeated_question', 'pending'},
    'review': {'pass', 'misdiagnosis', 'missed_error', 'alternative_rejected', 'answer_exposure', 'pending'},
    'pilot': {'independent_completed', 'assisted', 'interrupted', 'ongoing', 'unknown', 'pending'},
    'report_pair': {'needs_improvement', 'improved', 'not_improved', 'pending'},
}


class AssessmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_kind: Literal['task_pair', 'difficulty', 'coaching', 'review', 'pilot', 'report_pair']
    target_id: str = Field(min_length=1, max_length=100)
    paired_target_id: str | None = None
    target_version: str = Field(min_length=1, max_length=100)
    paired_target_version: str | None = None
    reviewer: str = Field(min_length=1, max_length=100)
    source: Literal['human', 'learner'] = 'human'
    criteria_version: str = Field(default='quality-metrics-v2', min_length=1, max_length=100)
    result: str
    note: str = Field(default='', max_length=4000)
    sample_id: str | None = None
    repetition: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def verdict(self):
        if self.result not in RESULTS[self.target_kind]:
            raise ValueError('result is not valid for target kind')
        if self.source == 'learner' and (self.target_kind != 'difficulty' or self.result not in {'too_easy', 'appropriate', 'too_hard', 'pending'}):
            raise ValueError('learner response must be difficulty feedback')
        if bool(self.sample_id) != bool(self.repetition):
            raise ValueError('sample_id and repetition must be supplied together')
        if (self.target_kind == 'task_pair' or self.target_kind == 'report_pair' and self.result not in {'needs_improvement', 'pending'}) and not self.paired_target_id:
            raise ValueError('paired target is required')
        return self


class AssessmentPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)
    result: str
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(default='', max_length=4000)


def initialize(store):
    with store.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS quality_assessments (assessment_id text, revision integer, payload jsonb NOT NULL, PRIMARY KEY(assessment_id,revision))')


def _target(conn, kind, identifier):
    table, key = {'task_pair': ('attempts', 'attempt_id'), 'difficulty': ('attempts', 'attempt_id'),
                  'review': ('reviews', 'record_id'), 'report_pair': ('reports', 'record_id'),
                  'pilot': ('pilot_records', 'attempt_id'), 'coaching': ('training_messages', 'action_id')}[kind]
    row = conn.execute(f'SELECT payload FROM {table} WHERE {key}=%s FOR SHARE', (identifier,)).fetchone()
    if not row and kind == 'review':
        row = conn.execute('SELECT payload FROM quality_runs WHERE run_id=%s FOR SHARE', (identifier,)).fetchone()
    if not row:
        raise DomainError('not_found', '판정할 저장 대상을 찾을 수 없습니다.', 404)
    return row['payload']


def target_version(value):
    return str(value.get('evaluation_version') or value.get('rules_version') or value.get('report_version') or value.get('contract_version') or value.get('release_version') or 'v1')


def validate_target(conn, data):
    target = _target(conn, data.target_kind, data.target_id)
    if data.target_kind == 'review' and 'samples' in target:
        sample = next((sample for sample in target['samples'] if sample['id'] == data.sample_id), None)
        if not sample or not any(result.get('repetition') == data.repetition for result in sample.get('results', [])):
            raise DomainError('invalid_sample', '실제 평가된 표본과 회차를 선택하세요.', 422)
    if target_version(target) != data.target_version:
        raise DomainError('target_version', '연결 대상 버전이 일치하지 않습니다.', 422)
    attempt_ids = [target['attempt_id']] if target.get('attempt_id') else [data.target_id] if data.target_kind in {'difficulty', 'task_pair', 'pilot'} else []
    if data.paired_target_id:
        if data.paired_target_id == data.target_id:
            raise DomainError('invalid_pair', '서로 다른 대상을 연결하세요.', 422)
        paired = _target(conn, data.target_kind, data.paired_target_id)
        if target_version(paired) != data.paired_target_version:
            raise DomainError('target_version', '비교 대상 버전이 일치하지 않습니다.', 422)
        if data.target_kind == 'report_pair' and target.get('attempt_id') != paired.get('attempt_id'):
            raise DomainError('invalid_pair', '같은 훈련의 제출본을 연결하세요.', 422)
        attempt_ids.append(paired.get('attempt_id', data.paired_target_id))
    # Filters anchor to target occurrence, never verdict update time.
    return dict(attempt_ids=attempt_ids, target_occurred_at=target.get('created_at') or target.get('started_at'),
                domain=target.get('domain'), difficulty=target.get('difficulty'), task_kind=target.get('task_kind'),
                evaluation_version=data.target_version)


def latest(conn):
    return [row['payload'] for row in conn.execute('SELECT DISTINCT ON (assessment_id) payload FROM quality_assessments ORDER BY assessment_id,revision DESC')]


def create(store, data):
    with store.connect() as conn:
        context = validate_target(conn, data)
        value = dict(data.model_dump(), **context, assessment_id=str(uuid.uuid4()), revision=1, previous_revision=None, recorded_at=now())
        conn.execute('INSERT INTO quality_assessments VALUES(%s,1,%s)', (value['assessment_id'], Jsonb(value)))
    return value


def patch(store, assessment_id, data):
    with store.connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (assessment_id,))
        row = conn.execute('SELECT payload FROM quality_assessments WHERE assessment_id=%s ORDER BY revision DESC LIMIT 1', (assessment_id,)).fetchone()
        if not row:
            raise DomainError('not_found', '판정을 찾을 수 없습니다.', 404)
        value = row['payload']
        if value['revision'] != data.expected_revision:
            raise DomainError('revision_conflict', '다른 판정 수정과 충돌했습니다.', 409)
        merged = dict(value, result=data.result, reviewer=data.reviewer, note=data.note)
        model = AssessmentInput.model_validate({k: merged[k] for k in AssessmentInput.model_fields})
        validate_target(conn, model)
        merged.update(previous_revision=value['revision'], revision=value['revision'] + 1, recorded_at=now())
        conn.execute('INSERT INTO quality_assessments VALUES(%s,%s,%s)', (assessment_id, merged['revision'], Jsonb(merged)))
    return merged


def delete_for_attempt(conn, attempt_id):
    conn.execute("DELETE FROM quality_assessments WHERE payload->'attempt_ids' @> %s", (Jsonb([attempt_id]),))


def routes(app, store):
    router = APIRouter()

    @router.get('/api/quality/assessments')
    def list_assessments():
        with store.connect() as conn:
            return {'assessments': latest(conn), 'history': [r['payload'] for r in conn.execute('SELECT payload FROM quality_assessments ORDER BY assessment_id,revision')]}

    @router.post('/api/quality/assessments')
    def create_assessment(data: AssessmentInput):
        return create(store, data)

    @router.patch('/api/quality/assessments/{assessment_id}')
    def edit_assessment(assessment_id: str, data: AssessmentPatch):
        return patch(store, assessment_id, data)

    app.include_router(router)
