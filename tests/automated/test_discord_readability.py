from da_agent.discord_education import representative_task
from da_agent.discord_service import format_task_intro
from da_agent.discord_transport import safe_chunks
from da_agent.discord_tables import dictionary_tables


def test_public_conditions_remain_visible_in_readable_sections():
    task = representative_task()
    doc = dict(task=task, session_id='sample', state='analysis', executions=[], reports=[])
    messages = format_task_intro(doc)
    rendered = '\n'.join(messages)
    assert len(messages) == 6
    assert '"objective"' not in rendered
    assert task['objective'] in messages[1]
    assert '2026-09-15까지 (종료일 제외)' in rendered
    tables = dictionary_tables(task)
    for name, table in task['dictionary'].items():
        image_table = next(t for t in tables if t['title'].endswith(name))
        assert table['description'] in rendered
        for column, kind in table['columns'].items():
            assert [column, kind] in image_table['rows']
    for criterion in task['rubric']['criteria']:
        for key in ('name', 'required', 'core_error', 'advanced'):
            assert criterion[key] in rendered
    assert task['quality_information']['collection'] in rendered
    assert all(limit in rendered for limit in task['accepted_limits'])
    assert '같은 오류는 중복 감점하지 않습니다' in rendered
    assert '도움받기 전후' in rendered
    assert 'variant' not in rendered and 'valid_paths' not in rendered
    assert all(len(safe_chunks(message)) == 1 for message in messages)


def test_followup_dates_and_help_level_are_not_hardcoded():
    task = representative_task(variant='followup', difficulty='beginner')
    rendered = '\n'.join(format_task_intro(dict(task=task, session_id='sample', state='completed', executions=[{}], reports=[{}])))
    assert '2026-09-08부터 2026-09-22까지' in rendered
    assert '상태: 완료' in rendered and '기본 도움: 안내 포함' in rendered


def test_chunking_preserves_paragraphs_and_escapes_mentions():
    assert safe_chunks('가' * 12 + '\n\n' + '나' * 12, limit=20) == ['가' * 12, '\n\n' + '나' * 12]
    chunks = safe_chunks('@everyone **가**' * 20, limit=40)
    assert all(len(chunk) <= 40 and '@everyone' not in chunk for chunk in chunks)
    assert all((len(chunk) - len(chunk.rstrip('\\'))) % 2 == 0 for chunk in chunks)
    assert safe_chunks('') == ['(내용 없음)']
