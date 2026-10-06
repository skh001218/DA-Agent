"""Check concurrent naming only in an explicitly configured disposable database."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import uuid

from da_agent.discord_store import DiscordStore
from da_agent.errors import DomainError


def main():
    store = DiscordStore(os.environ['DISCORD_TEST_RECORDS_DSN'])
    store.initialize()
    owner = 'title-probe-' + uuid.uuid4().hex
    ids = []
    for _ in range(8):
        sid = uuid.uuid4().hex
        doc = dict(session_id=sid, owner_user_id=owner, guild_id='test', channel_id='test',
                   thread_id=None, difficulty='intermediate', task={'title': '튜토리얼 완료율 변화와 우선 대응'})
        store.create(doc, uuid.uuid4().hex, {})
        ids.append(sid)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda sid: store.reserve_thread_name(owner, sid), ids))
    assert sorted(v['thread_name_ordinal'] for v in values) == list(range(1, 9))
    assert len({v['thread_name'] for v in values}) == 8
    assert all(len(v['thread_name']) <= 40 for v in values)
    with ThreadPoolExecutor(max_workers=8) as pool:
        retries = list(pool.map(lambda sid: store.reserve_thread_name(owner, sid), ids))
    assert [v['thread_name'] for v in retries] == [v['thread_name'] for v in values]
    try:
        store.reserve_thread_name('other', ids[0])
        raise AssertionError('Foreign owner must be rejected')
    except DomainError:
        pass
    print(json.dumps(dict(concurrent_requests=8, unique_titles=8, retry_stable=True,
                         foreign_owner_rejected=True, maximum_title_length=max(len(v['thread_name']) for v in values))))


if __name__ == '__main__':
    main()
