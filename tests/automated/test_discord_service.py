"""Use only a separate disposable PostgreSQL instance for Discord state tests."""
import concurrent.futures
import os
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from da_agent.discord_store import DiscordStore
from da_agent.discord_service import DiscordTrainingService, format_result
from da_agent.errors import DomainError


@pytest.fixture
def store():
    dsn = os.getenv('DISCORD_TEST_RECORDS_DSN')
    if not dsn:
        pytest.skip('Requires explicit disposable DISCORD_TEST_RECORDS_DSN')
    value = DiscordStore(dsn)
    value.initialize()
    yield value


@pytest.fixture
def service(store):
    settings = SimpleNamespace(daily_call_limit=2, max_rows=1000, max_bytes=1048576, query_timeout_ms=5000)
    return DiscordTrainingService(store, SimpleNamespace(runner=Mock()), Mock(), settings,
        dataset_factory=lambda settings, task: {'schema_name': 'discord_test_fixture', 'data_version': task['data_version']})


def start(service, owner=None):
    owner = owner or uuid.uuid4().hex
    session = service.start(owner, 'guild', 'channel', uuid.uuid4().hex)
    assert 'session_id' in session, session
    return owner, session


def test_owner_checked_before_event_model_or_mutation(service):
    owner, session = start(service)
    event = uuid.uuid4().hex
    with pytest.raises(DomainError, match='자신의'):
        service.handle('other', session['session_id'], event, 'query', '조회')
    service.provider.review.assert_not_called()
    with service.store.connect() as conn:
        assert not conn.execute('SELECT 1 FROM discord_records.events WHERE event_id=%s', (event,)).fetchone()


def test_duplicate_and_restart_restore_report_followup_and_stop(service):
    owner, session = start(service)
    sid = session['session_id']
    event = uuid.uuid4().hex
    first = service.handle(owner, sid, event, 'report', '관측 한계가 있으므로 원인을 보류하고 추가 로그를 확인한다.')
    assert first == service.handle(owner, sid, event, 'report', '다른 텍스트')
    assert len(service.get_session(owner, sid)['reports']) == 1
    response = service.handle(owner, sid, uuid.uuid4().hex, 'submit')
    assert '후속 질문' in response['messages'][0]
    service.handle(owner, sid, uuid.uuid4().hex, 'followup', '추가 실험으로 비교한다.')
    restarted = DiscordTrainingService(DiscordStore(service.store.dsn), service.engine, service.provider, service.settings)
    restored = restarted.resume(owner, 'guild', sid)['session']
    assert restored['reports'][0]['followup_answers'][0]['text'] == '추가 실험으로 비교한다.'
    restarted.handle(owner, sid, uuid.uuid4().hex, 'end')
    assert restarted.get_session(owner, sid)['state'] == 'stopped'
    restarted.handle(owner, sid, uuid.uuid4().hex, 'continue')
    assert restarted.get_session(owner, sid)['state'] == 'reporting'


def test_event_claim_atomic_across_workers_and_uncertain_after_crash(store):
    owner, event = uuid.uuid4().hex, uuid.uuid4().hex
    def claim(_):
        return store.claim_event(event, owner)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(claim, range(8)))
    assert sum(value is None for value in values) == 1
    assert all(value is None or value['event_state'] == 'uncertain' for value in values)
    restarted = DiscordStore(store.dsn)
    assert restarted.claim_event(event, owner)['event_state'] == 'uncertain'
    with pytest.raises(DomainError):
        restarted.claim_event(event, 'other')


def test_atomic_user_daily_limit_does_not_block_record_read(service):
    owner, session = start(service)
    def reserve(_):
        try:
            return service.store.reserve_call(owner, 2)
        except DomainError:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(reserve, range(6)))
    assert sorted(value for value in values if value is not None) == [1, 2]
    assert service.get_session(owner, session['session_id'])


def test_concurrent_reports_keep_versions_ordered(service):
    owner, session = start(service)
    def report(index):
        return service.handle(owner, session['session_id'], uuid.uuid4().hex, 'report', f'보고 {index}')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(report, range(4)))
    assert [report['version'] for report in service.get_session(owner, session['session_id'])['reports']] == [1, 2, 3, 4]


def test_report_append_retains_original_versions_and_start_replay_binding(service):
    owner, session = start(service)
    sid = session['session_id']
    service.handle(owner, sid, uuid.uuid4().hex, 'report', '첫 문단')
    response = service.handle(owner, sid, uuid.uuid4().hex, 'report', '다음 문단', {'append': True})
    assert response['session']['reports'][0]['content']['report_text'] == '첫 문단'
    assert response['session']['reports'][1]['content']['report_text'] == '첫 문단\n다음 문단'
    event_id = uuid.uuid4().hex
    another = service.start(owner, 'guild', 'channel', event_id)
    service.bind_thread(owner, another['session_id'], uuid.uuid4().hex)
    assert service.start(owner, 'guild', 'channel', event_id)['thread_id']


def test_sessions_guild_isolation_and_bind_thread(service):
    owner, session = start(service)
    other, other_session = start(service)
    assert [item['session_id'] for item in service.list_sessions(owner, 'guild')] == [session['session_id']]
    assert not service.list_sessions(owner, 'different')
    with pytest.raises(DomainError):
        service.resume(owner, 'different', session['session_id'])
    service.bind_thread(owner, session['session_id'], uuid.uuid4().hex)
    with pytest.raises(DomainError):
        service.bind_thread(other, session['session_id'], 'bad')


def test_result_display_distinguishes_empty_null_preview_and_collection_limit():
    settings = SimpleNamespace(max_rows=1000, max_bytes=1048576, query_timeout_ms=5000)
    execution = {'execution_id':'e', 'conditions':{'denominator':'users'}, 'data_version':'v',
        'result': {'rows': [], 'columns': [{'name':'rate'}], 'result_complete': True}}
    assert '빈 결과' in format_result(execution, settings)
    execution['result']['rows'] = [[None]] * 12
    text = format_result(execution, settings)
    assert '분모 0' in text and '표시만' in text and '불완전한' not in text
    execution['result']['result_complete'] = False
    assert '전체 건수는 알 수 없습니다' in format_result(execution, settings)


def test_help_level_separate_from_difficulty_and_invalid_setting_fails(service):
    owner = uuid.uuid4().hex
    session = service.start(owner, 'guild', 'channel', uuid.uuid4().hex, difficulty='advanced', help_level='guided')
    assert session['difficulty'] == 'advanced' and session['help_level'] == 'guided'
    assert session['task']['help_policy']['default_level'] == 'guided'
    result = service.start(owner, 'guild', 'channel', uuid.uuid4().hex, help_level='invalid')
    assert result['state'] == 'failed'
