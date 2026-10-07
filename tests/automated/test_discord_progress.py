import asyncio
from threading import Event
from types import SimpleNamespace as NS

import pytest

from da_agent.discord_generation import status_message
from da_agent.discord_progress import progress_message, snapshot
from da_agent.errors import DomainError
from test_discord_generation import generated_service
from test_discord_transport import Gateway, setup


def test_generation_reports_only_committed_public_steps(generated_service):
    service, session, _ = generated_service
    reports = []

    def report(progress):
        assert progress == snapshot(service.get_session('owner', session['session_id'])['generation'])
        reports.append(progress)

    ready = service.generate('owner', session['session_id'], progress=report)
    assert [p['completed_steps'] for p in reports] == [0, 1, 2, 3]
    assert [p['status'] for p in reports] == ['planning', 'preparing_data', 'validating', 'ready']
    assert ready['generation']['completed_steps'] == 3
    assert all(set(p) == {'status', 'completed_steps'} for p in reports)
    service.generate('owner', session['session_id'], progress=report)
    assert len(reports) == 4  # Replaying ready work emits no fresh progress.


def test_failed_stage_preserves_completed_steps_and_retry_resets(generated_service):
    service, session, stage = generated_service
    reports = []
    stage.side_effect = DomainError('validation_failed', '자료 검증 실패')
    failed = service.generate('owner', session['session_id'], progress=reports.append)
    assert failed['generation']['status'] == 'failed'
    assert reports[-1] == {'status': 'failed', 'completed_steps': 1}
    assert '33%' in status_message(failed) and '100%' not in status_message(failed)
    stage.side_effect = None
    reports.clear()
    ready = service.handle('owner', session['session_id'], 'retry-progress', 'retry', progress=reports.append)
    assert ready['session']['generation']['status'] == 'ready'
    assert [p['completed_steps'] for p in reports] == [0, 1, 2, 3]


def test_cancelled_stage_never_claims_completion(generated_service):
    service, session, stage = generated_service
    reports = []

    def cancel(*args, **kwargs):
        service.handle('owner', session['session_id'], 'end-progress', 'end')
        raise DomainError('cancelled', '출제 취소')

    stage.side_effect = cancel
    result = service.generate('owner', session['session_id'], progress=reports.append)
    assert reports[-1] == {'status': 'cancelled', 'completed_steps': 1}
    assert '33%' in status_message(result) and '출제 취소' in progress_message(reports[-1])


def test_clarification_answer_reports_new_attempt(generated_service):
    service, session, _ = generated_service
    service.provider.generation_mode = 'synthetic'
    sid = session['session_id']
    with service.store.edit('owner', sid) as (doc, _):
        doc['generation'].update(status='needs_clarification', completed_steps=0, questions=['기간은?'])
    reports = []
    result = service.handle('owner', sid, 'answer-progress', 'answer', '9월 한 달', progress=reports.append)
    assert reports[0] == {'status': 'planning', 'completed_steps': 0}
    assert reports[-1] == {'status': 'ready', 'completed_steps': 3}
    assert result['session']['generation']['revision'] == 1


def test_observer_failure_does_not_change_generation(generated_service):
    service, session, _ = generated_service

    def broken(progress):
        raise RuntimeError('private detail')

    result = service.generate('owner', session['session_id'], progress=broken)
    assert result['generation']['status'] == 'ready'


def test_publication_failure_never_reports_one_hundred(generated_service, monkeypatch):
    service, session, _ = generated_service
    reports = []
    def fail(package):
        raise DomainError('publication_failed', '공개 실패')
    monkeypatch.setattr('da_agent.discord_generation.publish', fail)
    result = service.generate('owner', session['session_id'], progress=reports.append)
    assert reports[-1] == {'status': 'failed', 'completed_steps': 2}
    assert '67%' in status_message(result)
    assert all('100%' not in progress_message(p) for p in reports)


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'interrupted', 'needs_clarification'])
def test_legacy_terminal_records_do_not_invent_percent(status):
    assert '확인 불가' in progress_message({'status': status})
    assert '%' not in progress_message({'status': status})


class ProgressGateway(Gateway):
    def __init__(self):
        super().__init__()
        self.edits = []
        self.first_update = Event()
        self.fail_edit = False

    async def send(self, channel, text):
        await super().send(channel, text)
        return NS(id=len(self.sent))

    async def edit_progress(self, message, text):
        self.first_update.set()
        if self.fail_edit:
            raise RuntimeError('secret transport error')
        self.edits.append((message.id, text))


def progress_setup():
    service, _, transport, event = setup()
    gateway = ProgressGateway()
    transport.gateway = gateway
    service.session.update(generation={'status': 'accepted'}, state='accepted')
    return service, gateway, transport, event


def test_training_updates_one_message_before_generation_finishes():
    service, gateway, transport, event = progress_setup()

    def generate(owner, sid, *, progress):
        for status in ('planning', 'preparing_data'):
            service.session['generation']['status'] = status
            progress(snapshot(service.session['generation']))
        assert gateway.first_update.wait(2), 'Progress must be visible while the worker is still running'
        for status in ('validating', 'ready'):
            service.session['generation']['status'] = status
            progress(snapshot(service.session['generation']))
        return dict(service.session)

    service.generate = generate
    asyncio.run(transport.command(event, 'training'))
    assert gateway.sent[0][0] == 30 and '0%' in gateway.sent[0][1]
    assert [mid for mid, _ in gateway.edits] == [1, 1, 1]
    assert ['33%' in gateway.edits[0][1], '67%' in gateway.edits[1][1], '100%' in gateway.edits[2][1]] == [True] * 3
    assert gateway.sent[-1][1] == '업무 담당자: 준비됨'
    assert not transport._generation_displays


@pytest.mark.parametrize('action', ['retry', 'answer'])
def test_slash_retry_and_answer_use_progress(action):
    service, gateway, transport, event = progress_setup()
    event.channel = gateway.thread
    service.session.update(thread_id='30')
    service.session['generation']['status'] = 'failed' if action == 'retry' else 'needs_clarification'

    def handle(*args, progress, **kwargs):
        for status in ('planning', 'preparing_data', 'validating', 'ready'):
            progress(snapshot({'status': status}))
        return {'session': {**service.session, 'generation': {'status': 'ready'}}, 'messages': ['완료']}

    service.handle = handle
    asyncio.run(transport.command(event, action, text='추가 조건'))
    assert len(gateway.edits) == 3 and '100%' in gateway.edits[-1][1]


def test_question_reply_also_updates_progress():
    service, gateway, transport, event = progress_setup()
    service.session.update(thread_id='30')
    service.session['generation']['status'] = 'needs_clarification'

    def handle(*args, progress, **kwargs):
        progress(snapshot({'status': 'planning'}))
        progress(snapshot({'status': 'ready'}))
        return {'session': {**service.session, 'generation': {'status': 'ready'}}, 'messages': ['완료']}

    service.handle = handle
    message = NS(id=101, author=event.user, guild=event.guild, channel=gateway.thread,
                 content='9월 한 달', reference=NS(message_id=99))
    asyncio.run(transport.message(message))
    assert '100%' in gateway.edits[-1][1]


def test_edit_failure_still_delivers_task():
    service, gateway, transport, event = progress_setup()
    gateway.fail_edit = True

    def generate(*args, progress):
        progress(snapshot({'status': 'planning'}))
        progress(snapshot({'status': 'ready'}))
        return {**service.session, 'generation': {'status': 'ready'}}

    service.generate = generate
    asyncio.run(transport.command(event, 'training'))
    assert gateway.sent[-1][1] == '업무 담당자: 준비됨'
    assert '과제 공간' in gateway.replies[-1]
    assert not transport._generation_displays


def test_cancel_updates_active_message_without_waiting_for_model():
    service, gateway, transport, event = progress_setup()
    release = Event()
    began = Event()

    def generate(*args, progress):
        progress(snapshot({'status': 'planning'}))
        progress(snapshot({'status': 'preparing_data'}))
        began.set()
        assert release.wait(3)
        # A queued/stale active update cannot replace the terminal cancellation.
        progress(snapshot({'status': 'validating'}))
        progress({'status': 'cancelled', 'completed_steps': 1})
        return {**service.session, 'generation': {'status': 'cancelled', 'completed_steps': 1}}

    async def check():
        service.generate = generate
        task = asyncio.create_task(transport.command(event, 'training'))
        try:
            assert await asyncio.to_thread(began.wait, 2)
            assert await asyncio.to_thread(gateway.first_update.wait, 2)
            cancelled = {**service.session, 'generation': {'status': 'cancelled', 'completed_steps': 1}}
            await transport._generation_ui(gateway.thread, event.user, cancelled)
            assert '33%' in gateway.edits[-1][1] and '출제 취소' in gateway.edits[-1][1]
        finally:
            release.set()
            await task
        assert '출제 취소' in gateway.edits[-1][1]
        assert not transport._generation_displays

    asyncio.run(check())
