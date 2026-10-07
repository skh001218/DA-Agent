"""Deterministic presentation of saved public task text and time boundaries."""
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KST = timezone(timedelta(hours=9))
ISO_DATE = re.compile(r'\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?')


def short_text(text, limit=100, sentences=1):
    """Use verbatim sentences; clipping is only a preview, never a new fact."""
    text = re.sub(r'\s+', ' ', text).strip()
    parts = re.split(r'(?<=[.!?。])\s+', text)
    result = ' '.join(parts[:sentences])
    return result if len(result) <= limit else result[:limit - 1].rstrip() + '…'


def public_sections(task):
    if task.get('intro_sections'):
        return task['intro_sections']
    # These labels are authored by task_quality.public_description. Only parse
    # that public serialization; never infer questions from coaching valid_paths.
    text = task['objective']
    blocks = text.split('\n\n')
    labelled = {}
    for block in blocks:
        for label in ('업무 배경', '관측된 문제', '판단할 업무 결정', '분석 과제', '에이전트가 설정한 연습 조건'):
            if block.startswith(label + ':'):
                labelled[label] = block[len(label) + 1:].strip()
    questions, judgments = [], []
    for line in labelled.get('분석 과제', '').splitlines():
        if line.startswith('- '):
            questions.append(line[2:])
            judgments.append('')
        elif judgments:
            judgments[-1] += ('\n' if judgments[-1] else '') + line.strip()
    return {'background':blocks[0],
        'context':[labelled[key] for key in ('업무 배경', '관측된 문제') if key in labelled],
        'decision':labelled.get('판단할 업무 결정', ''), 'questions':questions, 'judgments':judgments,
        'conditions':[line[2:] for line in labelled.get('에이전트가 설정한 연습 조건', '').splitlines() if line.startswith('- ')]}


def background_preview(task):
    sections = public_sections(task)
    context = sections.get('context') or [sections.get('background') or task['objective']]
    sentences = [short_text(value, 89) for value in context[:2]]
    return '\n'.join(dict.fromkeys(sentences))


def _source_timezone(task, description):
    if re.search(r'\bUTC\b', description):
        return timezone.utc
    if re.search(r'\bKST\b|한국\s*시간', description):
        return KST
    name = task.get('timezone')
    if name in ('Asia/Seoul', 'KST'):
        return KST
    if name == 'UTC':
        return timezone.utc
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _kst(value, source):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        if source is None:
            raise ValueError('An explicit source timezone is required')
        parsed = parsed.replace(tzinfo=source)
    return parsed.astimezone(KST)


def time_label(value, year=None, date_only=False):
    prefix = f'{value.year}/' if year is None or year != value.year else ''
    result = prefix + f'{value.month}/{value.day}'
    if date_only:
        return result
    result += value.strftime(' %H:%M')
    if value.second or value.microsecond:
        result += value.strftime(':%S')
        if value.microsecond:
            result += f'.{value.microsecond:06d}'.rstrip('0')
    return result


def period_lines(task):
    """Convert the public observation ranges, not inferred min/max data dates."""
    period = task['period']
    description = period['description']
    source = _source_timezone(task, description)
    matches = list(ISO_DATE.finditer(description))
    ranges = []
    try:
        if len(matches) >= 2 and len(matches) % 2 == 0:
            previous_end = 0
            for index in range(0, len(matches), 2):
                start, end = matches[index:index + 2]
                connector = description[start.end():end.start()].strip()
                if connector not in ('~', '～', '–', '—', '부터', '이상 ~'):
                    raise ValueError('Unrecognized date range')
                a, b = _kst(start.group(), source), _kst(end.group(), source)
                if b < a:
                    raise ValueError('Reversed date range')
                prefix = description[previous_end:start.start()]
                label = re.search(r'window_([a-z])\s*:', prefix, re.I)
                label = '기간 ' + label.group(1).upper() if label else '기간 ' + str(index // 2 + 1)
                suffix = description[end.end():matches[index + 2].start() if index + 2 < len(matches) else len(description)]
                excluded = period.get('end_exclusive') is True or bool(re.search(r'미만|종료일\s*제외|끝\s*제외', suffix))
                if len(start.group()) == len(end.group()) == 10 and source != KST and not excluded:
                    raise ValueError('A closed date range needs an explicit time boundary for conversion')
                date_only = len(start.group()) == len(end.group()) == 10 and source == KST
                ranges.append((label, a, b, excluded, date_only))
                previous_end = end.end()
        elif len(matches) == 1 and len(matches[0].group()) == 10 and re.search(r'하루|당일', description):
            a = _kst(matches[0].group(), source)
            b = a + timedelta(days=1)
            ranges.append(('', a, b, True, source == KST))
    except ValueError:
        ranges = []
    if not ranges:
        return ['• 관측 기간: ' + short_text(description, 160), '• 정확한 기간·시간 기준: /help kind:문제 원문']
    years = {value.year for _, a, b, _, _ in ranges for value in (a, b)}
    year = next(iter(years)) if len(years) == 1 else None
    lines = ['• 관측 기간: 한국 시간' + (f' · {year}년' if year else '')]
    for label, a, b, excluded, date_only in ranges:
        if not label and date_only:
            lines.append('  ' + time_label(a, year, True) + ' 하루')
        else:
            lines.append('  ' + (label + ': ' if label else '') + time_label(a, year, date_only)
                + ' ~ ' + time_label(b, year, date_only) + (' 미만' if excluded else ''))
    if period.get('observation_end'):
        try:
            cutoff = _kst(period['observation_end'], _source_timezone(task, ''))
            lines.append('• 자료 관측 종료: ' + time_label(cutoff, year) + ' 미만')
        except ValueError:
            lines.append('• 자료 관측 종료: 원문 확인')
    return lines


def task_details(task):
    """Keep exact original wording and every condition available without a model."""
    sections = public_sections(task)
    original = task['objective']
    lines = ['문제 원문 · 전체 조건', '', original]
    extra = [value for value in [sections.get('background', ''), *sections.get('context', []), sections.get('decision', '')]
        if value and value not in original]
    if extra:
        lines.extend(['', '공개 배경과 업무 결정', *extra])
    lines.extend(['', '분석 질문과 판단 조건'])
    judgments = sections.get('judgments', [])
    for index, question in enumerate(sections.get('questions', [])):
        if question not in original:
            lines.append(f'{index + 1}. {question}')
        if index < len(judgments) and judgments[index] and judgments[index] not in original:
            lines.append(judgments[index])
    lines.extend(['', '관측 기간 원문: ' + task['period']['description'], '자료 시간 기준: ' + task['timezone']])
    if task['period'].get('observation_end'):
        lines.append('자료 관측 종료 원문: ' + task['period']['observation_end'] + ' 미만')
    lines.extend('연습 조건: ' + value for value in sections.get('conditions', []) if value not in original)
    lines.extend('인정할 한계: ' + value for value in task.get('accepted_limits', []))
    quality = task.get('quality_information', {})
    lines.extend(key + ': ' + quality[key] for key in ('collection', 'verification_scope') if key in quality)
    return '\n'.join(lines)
