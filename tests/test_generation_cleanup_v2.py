"""Cleanup safety tests. Recorder mocks are explicit; optional data DB uses
only an independently created random test package/schema, never shared data.
"""
import datetime as dt
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from da_agent import generation_cleanup as cleanup
from da_agent.capabilities import approve, RULES_VERSION
from da_agent.task_contracts import RuleApproval
from da_agent.errors import DomainError

AT = dt.datetime(2026, 10, 4, 12, tzinfo=dt.timezone.utc)


class Result:
    def __init__(self, rows=()): self.rows = list(rows)
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def __iter__(self): return iter(self.rows)


class RecorderMock:
    """Only recorder queries mocked; artifact paths are real pytest temp dirs."""
    def __init__(self, identity, age_hours=25, attempts=(), samples=(), recheck_attempt=False, active=False, recheck_sample=False):
        self.jobs = [{'private': {'package_id': identity}, 'payload': {'created_at': (AT-dt.timedelta(hours=age_hours)).isoformat(), 'status': 'failed'}}]
        self.attempts, self.samples = list(attempts), list(samples)
        self.recheck_attempt, self.active = recheck_attempt, active
        self.recheck_sample = recheck_sample
        self.statements = []
    def connect(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, query, params=None):
        self.statements.append((query, params))
        if query.startswith('SELECT j.private'): return Result(self.jobs)
        if query == 'SELECT payload FROM attempts': return Result([{'payload': {'package_id': x}} for x in self.attempts])
        if query == 'SELECT payload FROM generation_samples': return Result([{'payload': {'package_id': x}} for x in self.samples])
        if query.startswith('SELECT 1 FROM attempts'): return Result([{'found': 1}] if self.recheck_attempt else [])
        if query.startswith('SELECT 1 FROM generation_samples'): return Result([{'found': 1}] if self.recheck_sample else [])
        if query.startswith('SELECT 1 FROM training_requests'): return Result([{'found': 1}] if self.active else [])
        return Result()


class DataDbMock:
    """Never connects to PostgreSQL; captures the exact DROP identifier."""
    def __init__(self): self.statements = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, query): self.statements.append(query.as_string())


def artifact(tmp_path):
    from da_agent.data import generate_package
    from da_agent.packages import PackageCatalog
    identity = 'generated-' + uuid.uuid4().hex[:24]
    catalog = PackageCatalog(tmp_path / 'catalog')
    package = generate_package(catalog.root, identity, 'v1', 42, 50, 'baseline')
    return identity, catalog, package


def mock_data_db(monkeypatch):
    database = DataDbMock()
    monkeypatch.setattr(cleanup, 'generator_dsn', lambda: 'mock-database')
    monkeypatch.setattr(cleanup.psycopg, 'connect', lambda dsn: database)
    return database


@pytest.mark.parametrize('age', [0, 23, 23.999])
def test_under_twenty_four_hours_is_never_deleted(tmp_path, monkeypatch, age):
    identity, catalog, package = artifact(tmp_path)
    database = mock_data_db(monkeypatch)
    result = cleanup.prune(RecorderMock(identity, age_hours=age), catalog, AT)
    assert result['removed_package_ids'] == [] and package.path.exists()
    assert not database.statements


@pytest.mark.parametrize('reference', ['attempt', 'sample', 'late_attempt', 'active_request', 'late_sample'])
def test_referenced_or_active_artifact_is_preserved(tmp_path, monkeypatch, reference):
    identity, catalog, package = artifact(tmp_path)
    database = mock_data_db(monkeypatch)
    recorder = RecorderMock(identity, attempts=[identity] if reference == 'attempt' else [], samples=[identity] if reference == 'sample' else [], recheck_attempt=reference == 'late_attempt', active=reference == 'active_request', recheck_sample=reference == 'late_sample')
    assert cleanup.prune(recorder, catalog, AT)['removed_package_ids'] == []
    assert package.path.exists() and not database.statements


@pytest.mark.parametrize('identity', ['../outside', 'generated-' + 'G'*24, 'training-001', 'sample-' + 'a'*24, 'generated-' + 'a'*23])
def test_invalid_identity_never_loads_or_deletes(tmp_path, monkeypatch, identity):
    database = mock_data_db(monkeypatch)
    outside = tmp_path / 'outside'
    outside.mkdir()
    def unsafe_load(*args, **kw): raise AssertionError('invalid identity reached loader')
    catalog = SimpleNamespace(root=tmp_path / 'catalog', load=unsafe_load)
    assert cleanup.prune(RecorderMock(identity), catalog, AT)['removed_package_ids'] == []
    assert outside.exists() and not database.statements


def test_foreign_package_path_and_invalid_schema_are_preserved(tmp_path, monkeypatch):
    identity, catalog, package = artifact(tmp_path)
    database = mock_data_db(monkeypatch)
    outside = tmp_path / 'outside'
    outside.mkdir()
    for fake in (SimpleNamespace(path=outside, schema_name=package.schema_name), SimpleNamespace(path=package.path, schema_name='public;DROP TABLE users')):
        mocked_catalog = SimpleNamespace(root=catalog.root, load=lambda *args, **kw: fake)
        assert cleanup.prune(RecorderMock(identity), mocked_catalog, AT)['removed_package_ids'] == []
        assert package.path.exists() and outside.exists()
    assert not database.statements


def test_symlink_escape_is_preserved_if_platform_supports_it(tmp_path, monkeypatch):
    identity = 'generated-' + uuid.uuid4().hex[:24]
    root = tmp_path / 'catalog'
    (root / identity).mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        (root / identity / 'v1').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Platform does not allow directory symlinks')
    database = mock_data_db(monkeypatch)
    catalog = SimpleNamespace(root=root, load=lambda *args, **kw: (_ for _ in ()).throw(AssertionError('escaped path reached loader')))
    assert cleanup.prune(RecorderMock(identity), catalog, AT)['removed_package_ids'] == []
    assert outside.exists() and not database.statements


def test_exact_aged_unreferenced_owned_artifact_only_is_removed(tmp_path, monkeypatch):
    identity, catalog, package = artifact(tmp_path)
    database = mock_data_db(monkeypatch)
    sibling = catalog.root / 'keep-me' / 'v1'
    sibling.mkdir(parents=True)
    (sibling / 'sentinel').write_text('preserve', encoding='utf-8')
    result = cleanup.prune(RecorderMock(identity, age_hours=24), catalog, AT)
    expected_schema = 'pkg_' + hashlib.sha256((identity + '/v1').encode()).hexdigest()[:20]
    assert result == {'removed_package_ids': [identity], 'minimum_age_hours': 24}
    assert database.statements == ['DROP SCHEMA IF EXISTS "' + expected_schema + '" CASCADE']
    assert not package.path.exists() and (sibling / 'sentinel').read_text() == 'preserve'


class ApprovalRecorderMock:
    def __init__(self, levels):
        self.samples = [{'sample_id': 'sample-' + str(i), 'payload': {'capability_id': 'access-design', 'difficulty': level, 'status': 'validated', 'rules_version': RULES_VERSION}} for i, level in enumerate(levels)]
        self.saved = []
    def connect(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, query, params=None):
        if query.startswith('SELECT sample_id'): return Result(self.samples)
        if query.startswith('INSERT INTO generation_approvals'): self.saved.append(params)
        return Result()


def test_approval_same_level_three_blocked_all_three_levels_allowed():
    for levels in (['beginner']*3, ['beginner', 'beginner', 'advanced']):
        store = ApprovalRecorderMock(levels)
        data = RuleApproval(reviewer='test reviewer', sample_ids=[s['sample_id'] for s in store.samples], rules_version=RULES_VERSION, result='approved')
        with pytest.raises(DomainError) as exc:
            approve(store, 'access-design', data)
        assert exc.value.code == 'invalid_samples' and not store.saved
    store = ApprovalRecorderMock(['beginner', 'intermediate', 'advanced'])
    data = RuleApproval(reviewer='test reviewer', sample_ids=[s['sample_id'] for s in store.samples], rules_version=RULES_VERSION, result='approved')
    assert approve(store, 'access-design', data)['result'] == 'approved'
    assert len(store.saved) == 1


def test_real_db_cleanup_drops_only_independently_created_test_schema(tmp_path):
    if os.getenv('RUN_DB_TESTS') != '1' or not (os.getenv('GENERATOR_DSN') or Path(os.getenv('GENERATOR_PASSWORD_FILE','/run/secrets/generator_password')).is_file()):
        pytest.skip('Requires dedicated generator account for isolated test schema')
    import psycopg
    from psycopg import sql
    from da_agent.package_validation import generator_dsn
    identity, catalog, package = artifact(tmp_path)
    expected = 'pkg_' + hashlib.sha256((identity + '/v1').encode()).hexdigest()[:20]
    assert package.schema_name == expected and package.path.resolve().is_relative_to(tmp_path.resolve())
    # Only this random test schema is created/deleted; recorder queries are mocks.
    with psycopg.connect(generator_dsn()) as conn:
        assert conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s', (expected,)).fetchone() is None
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(expected)))
    try:
        assert cleanup.prune(RecorderMock(identity), catalog, AT)['removed_package_ids'] == [identity]
        with psycopg.connect(generator_dsn()) as conn:
            assert conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s', (expected,)).fetchone() is None
        assert not package.path.exists()
    finally:
        with psycopg.connect(generator_dsn()) as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(expected)))
