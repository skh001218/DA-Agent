"""Saved-review observations remain provisional until an explicit human verdict."""
import uuid
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb
from .errors import DomainError
from .store import now

COMPETENCIES = {'problem_definition', 'metrics_aggregation', 'analysis_design', 'evidence_interpretation', 'limitations_next_actions'}
CRITERIA = {'problem_definition': 'problem_definition', 'sql_accuracy': 'metrics_aggregation',
            'analysis_approach': 'analysis_design', 'interpretation': 'evidence_interpretation',
            'next_actions': 'limitations_next_actions'}


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    level: Literal['beginner', 'intermediate', 'advanced', 'auto'] = 'auto'
    goal: str = Field(default='', max_length=1000)


class Override(BaseModel):
    model_config = ConfigDict(extra='forbid')
    observation_id: str
    disagree: bool
    reason: str = Field(min_length=1, max_length=2000)


class StatePatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    preferences: Preferences | None = None
    overrides: list[Override] = Field(default_factory=list, max_length=100)


def initialize(store):
    with store.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS learning_states (id integer PRIMARY KEY, payload jsonb NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS learning_state_revisions (revision integer PRIMARY KEY, payload jsonb NOT NULL)')
        value = {'state_revision': 0, 'updated_at': now(), 'observations': [], 'preferences': {'level': 'auto', 'goal': ''}, 'overrides': {}}
        conn.execute('INSERT INTO learning_states VALUES(1,%s) ON CONFLICT DO NOTHING', (Jsonb(value),))
        conn.execute('INSERT INTO learning_state_revisions VALUES(0,%s) ON CONFLICT DO NOTHING', (Jsonb(value),))


def review_observations(review, assessment=None):
    if review.get('status') != 'completed' or not isinstance(review.get('feedback'), dict):
        return []
    results = []
    for criterion in review['feedback'].get('criteria', []):
        competency = CRITERIA.get(criterion.get('key', criterion.get('id')))
        if not competency or not isinstance(criterion.get('score'), (int, float)):
            continue
        level = criterion.get('level')
        if type(level) is not int or not 0 <= level <= 4:
            level = None
        confirmed = bool(assessment and assessment.get('source') == 'human' and assessment.get('target_kind') == 'review'
                         and assessment.get('target_id') == review['review_id'] and assessment.get('result') == 'pass'
                         and assessment.get('target_version') == str(review.get('evaluation_version') or review.get('rules_version') or 'v1'))
        # No feedback prose, SQL, report text, or raw result is copied into state.
        results.append({'observation_id': f"{review['review_id']}:{competency}", 'competency': competency,
                        'behavior': 'saved_review_criterion', 'attempt_id': review['attempt_id'],
                        'report_id': review['report_id'], 'review_id': review['review_id'],
                        'evaluation_version': review.get('evaluation_version') or review.get('rules_version'),
                        'criterion_level': level, 'level': level, 'certainty': 'human_verified' if confirmed else 'provisional',
                        'assessment_id': assessment.get('assessment_id') if assessment else None,
                        'assessment_revision': assessment.get('revision') if assessment else None,
                        'valid': not assessment or assessment.get('result') in {'pass', 'pending'}, 'confirmed': confirmed})
    return results


def _save(conn, value):
    value = dict(value, state_revision=value['state_revision'] + 1, updated_at=now())
    conn.execute('UPDATE learning_states SET payload=%s WHERE id=1', (Jsonb(value),))
    conn.execute('INSERT INTO learning_state_revisions VALUES(%s,%s)', (value['state_revision'], Jsonb(value)))
    return value


def refresh(conn):
    value = conn.execute('SELECT payload FROM learning_states WHERE id=1 FOR UPDATE').fetchone()['payload']
    observations = []
    verdicts = {}
    # Old deployments may initialize learning state before the assessments layer.
    if conn.execute("SELECT to_regclass('quality_assessments') AS table_name").fetchone()['table_name']:
        for row in conn.execute('SELECT DISTINCT ON (assessment_id) payload FROM quality_assessments ORDER BY assessment_id,revision DESC'):
            verdict = row['payload']
            if verdict.get('target_kind') != 'review' or verdict.get('source') != 'human':
                continue
            key = verdict['target_id']
            if key not in verdicts or verdict.get('recorded_at', '') > verdicts[key].get('recorded_at', ''):
                verdicts[key] = verdict
    for row in conn.execute('SELECT payload FROM reviews ORDER BY record_id'):
        review = row['payload']
        if not conn.execute('SELECT 1 FROM reports WHERE record_id=%s AND attempt_id=%s', (review.get('report_id'), review.get('attempt_id'))).fetchone():
            continue
        observations.extend(review_observations(review, verdicts.get(review['review_id'])))
    active = {observation['observation_id'] for observation in observations}
    for row in conn.execute('SELECT revision,payload FROM learning_state_revisions'):
        snapshot = row['payload']
        changed = False
        for observation in snapshot['observations']:
            if observation['observation_id'] not in active and observation.get('valid', True):
                observation.update(valid=False, confirmed=False, certainty='invalidated', invalidation_reason='source_unavailable')
                changed = True
        if changed:
            conn.execute('UPDATE learning_state_revisions SET payload=%s WHERE revision=%s', (Jsonb(snapshot), row['revision']))
    # Keep identity, never claim mastery from a score. Missing evidence disappears.
    if value['observations'] != observations:
        value = dict(value, observations=observations,
                     overrides={key: val for key, val in value['overrides'].items() if any(o['observation_id'] == key for o in observations)})
        value = _save(conn, value)
    return value


def get(store):
    with store.connect() as conn:
        value = refresh(conn)
        history = [r['payload'] for r in conn.execute('SELECT payload FROM learning_state_revisions ORDER BY revision')]
    def status(key):
        evidence = [o for o in value['observations'] if o['competency'] == key and o['valid'] and not value['overrides'].get(o['observation_id'], {}).get('disagree')]
        return 'observed_verified' if any(o['confirmed'] for o in evidence) else 'observed_provisional' if evidence else 'unobserved'
    return dict(value, history=history, competencies={key: status(key) for key in sorted(COMPETENCIES)})


get_state = get


def patch(store, data):
    with store.connect() as conn:
        value = refresh(conn)
        if value['state_revision'] != data.expected_revision:
            raise DomainError('revision_conflict', '학습 상태가 변경되었습니다. 새로고침하세요.', 409)
        valid = {o['observation_id'] for o in value['observations']}
        for override in data.overrides:
            if override.observation_id not in valid:
                raise DomainError('not_found', '관측 근거를 찾을 수 없습니다.', 404)
        if data.preferences is not None:
            value['preferences'] = data.preferences.model_dump()
        for override in data.overrides:
            value['overrides'][override.observation_id] = dict(override.model_dump(), updated_at=now())
        return _save(conn, value)


def delete_for_attempt(conn, attempt_id):
    """Remove deleted evidence from all snapshots, preserving preference edits."""
    for row in conn.execute('SELECT revision,payload FROM learning_state_revisions'):
        payload = row['payload']
        removed = {o['observation_id'] for o in payload['observations'] if o.get('attempt_id') == attempt_id}
        payload['observations'] = [o for o in payload['observations'] if o.get('attempt_id') != attempt_id]
        payload['overrides'] = {k: v for k, v in payload['overrides'].items() if k not in removed}
        conn.execute('UPDATE learning_state_revisions SET payload=%s WHERE revision=%s', (Jsonb(payload), row['revision']))
    value = conn.execute('SELECT payload FROM learning_states WHERE id=1 FOR UPDATE').fetchone()['payload']
    removed = {o['observation_id'] for o in value['observations'] if o.get('attempt_id') == attempt_id}
    value['observations'] = [o for o in value['observations'] if o.get('attempt_id') != attempt_id]
    value['overrides'] = {k: v for k, v in value['overrides'].items() if k not in removed}
    _save(conn, value)


def routes(app, store):
    router = APIRouter()

    @router.get('/api/learning-state')
    def read_state():
        return get(store)

    @router.patch('/api/learning-state')
    def edit_state(data: StatePatch):
        return patch(store, data)

    app.include_router(router)
