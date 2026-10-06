"""Short public task names, stable across retries and thread recovery."""
import re
import unicodedata

TITLE_LIMIT = 40
DIFFICULTIES = {'beginner': '초급', 'intermediate': '중급', 'advanced': '고급'}


def thread_title_base(document):
    task = document.get('task') or {}
    difficulty = DIFFICULTIES.get(document.get('difficulty') or task.get('difficulty'), '연습')
    title = unicodedata.normalize('NFKC', str(task.get('title') or '분석 연습'))
    if document.get('practice') == 'analysis':
        title = '분석 · ' + title
    title = ''.join(c for c in title if not unicodedata.category(c).startswith('C') or c.isspace())
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'[@#\[\]`]', '', title).strip() or '분석 연습'
    return difficulty + ' - ' + title


def thread_title(base, ordinal=1):
    if ordinal < 1:
        raise ValueError('Thread ordinal must be positive')
    difficulty, separator, content = base.partition(' - ')
    if not separator:
        difficulty, content = '연습', base
    repeat = '' if ordinal == 1 else f' #{ordinal}'
    prefix = f'[{difficulty}{repeat} - '
    budget = TITLE_LIMIT - len(prefix) - 1
    if len(content) > budget:
        content = content[:budget - 1].rstrip() + '…'
    return prefix + content + ']'


def reserve_title(document, previous):
    """Caller holds a durable owner/guild/parent reservation lock."""
    if document.get('thread_name'):
        return
    base = thread_title_base(document)
    # Compare visible shortened names too: long descriptions may share a prefix.
    key = thread_title(base)
    ordinals = [int(d.get('thread_name_ordinal', 1)) for d in previous
                if d.get('thread_name') and thread_title(d.get('thread_name_base', thread_title_base(d))) == key]
    ordinal = max(ordinals, default=0) + 1
    document.update(thread_name=thread_title(base, ordinal), thread_name_base=base,
                    thread_name_ordinal=ordinal)
