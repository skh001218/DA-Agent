"""Public tabular payloads and deterministic Korean PNG rendering for Discord."""
import math
import os
import json
import re
from decimal import Decimal
from io import BytesIO
from pathlib import Path


def is_dictionary_request(text):
    if re.search(r'완료율|재방문율|도전\s*수|가입자\s*수|계산|비교', text):
        return False
    return bool(re.search(r'데이터\s*사전|data\s+dictionary|스키마|컬럼\s*(정보|설명|목록|자료형)', text, re.I))


def dictionary_tables(task, text=''):
    dictionary = task.get('dictionary', {})
    selected = [name for name in dictionary if re.search(r'\b' + re.escape(name) + r'\b', text, re.I)]
    return [dict(title=f'데이터 사전 · {name}',
                 subtitle=f"한 행: {table.get('unit', '미정')}\n{table.get('description', '')}",
                 columns=['컬럼명', '자료형'], rows=[[key, value] for key, value in table.get('columns', {}).items()],
                 after_message=0) for name, table in dictionary.items() if not selected or name in selected]


def result_table(execution):
    result = execution['result']
    names = {'group_value': '구분', 'denominator': '분모', 'numerator': '분자',
             'rate_percent': '비율 (%)', 'user_count': '고유 사용자 수',
             'attempts_count': '도전 이벤트 수', 'duplicate_attempts': '중복 도전 이벤트 수'}
    columns = [c['name'] if isinstance(c, dict) else str(c) for c in result.get('columns', [])]
    table = dict(title='조회 결과', subtitle='실행 ID: ' + execution['execution_id'],
                columns=[names.get(name, name) + ('\n' + name if name in names else '') for name in columns],
                rows=result.get('rows', [])[:10], after_message=0)
    if execution.get('conditions', {}).get('operation') == 'select':
        table['download'] = dict(filename='query-' + execution['execution_id'] + '.json',
                                content=json.dumps(dict(execution_id=execution['execution_id'],
                                    columns=result.get('columns', []), rows=result.get('rows', []),
                                    result_complete=result.get('result_complete', False),
                                    total_row_count=result.get('total_row_count')), ensure_ascii=False))
    return table


def cell_text(value):
    if value is None:
        return '값 없음 (NULL)'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return format(value, ',')
    if isinstance(value, float) and math.isfinite(value):
        return format(value, ',')
    if isinstance(value, str) and re.fullmatch(r'-?\d+\.\d+', value):
        return format(Decimal(value), ',f').rstrip('0').rstrip('.')
    return str(value)


def table_fallback(table):
    """Accessible row lists also used when attachment permission/rendering fails."""
    lines = [table['title'], table.get('subtitle', '')]
    for index, row in enumerate(table['rows'], 1):
        lines.append(f'행 {index}')
        lines.extend(f'• {name}: {cell_text(value)}' for name, value in zip(table['columns'], row))
    if not table['rows']:
        lines.append('빈 결과 (0행)')
    return '\n'.join(lines)


def _font_paths():
    pairs = [(os.environ.get('DISCORD_TABLE_FONT', ''), os.environ.get('DISCORD_TABLE_BOLD_FONT', '')),
             ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
             ('C:/Windows/Fonts/malgun.ttf', 'C:/Windows/Fonts/malgunbd.ttf')]
    for normal, bold in pairs:
        if normal and Path(normal).is_file():
            return normal, bold if bold and Path(bold).is_file() else normal
    raise RuntimeError('A Korean table font is required')


def render_table_png(table):
    """Return PNG pages; wrap by measured pixels without truncating cell values."""
    from PIL import Image, ImageDraw, ImageFont
    normal, bold = _font_paths()
    font = ImageFont.truetype(normal, 24)
    header = ImageFont.truetype(bold, 24)
    title_font = ImageFont.truetype(bold, 32)
    line_height, padding = 38, 18

    def wrap(text, face, width):
        lines = []
        for paragraph in str(text).split('\n'):
            line = ''
            for char in paragraph:
                if line and face.getlength(line + char) > width:
                    lines.append(line)
                    line = ''
                line += char
            lines.append(line)
        return lines

    # Separate wide results into named column panels; every cell remains visible.
    columns = table['columns'] or ['결과']
    rows = table['rows']
    pages = []
    for offset in range(0, len(columns), 4):
        names = columns[offset:offset + 4]
        width = 1200
        available = width - 64
        desired = []
        for i, name in enumerate(names):
            values = [cell_text(row[offset + i]) for row in rows]
            desired.append(max(220, min(650, max([header.getlength(name)] + [font.getlength(v) for v in values]) + padding * 2)))
        total = sum(desired)
        widths = [int(available * value / total) for value in desired]
        widths[-1] += available - sum(widths)
        title_lines = wrap(table['title'], title_font, available)
        subtitle_lines = wrap(table.get('subtitle', ''), font, available)
        top = 32 + len(title_lines) * 46 + 14 + len(subtitle_lines) * line_height + 24
        headers = [wrap(name, header, size - padding * 2) for name, size in zip(names, widths)]
        header_height = max(map(len, headers)) * line_height + padding * 2
        prepared = []
        for row in rows:
            values = row[offset:offset + len(names)]
            lines = [wrap(cell_text(value), font, size - padding * 2) for value, size in zip(values, widths)]
            prepared.append((values, lines, max(map(len, lines)) * line_height + padding * 2))
        # Paginate tall cells/rows rather than cut the image at an arbitrary height.
        batches, batch, height = [], [], top + header_height
        for item in prepared:
            if batch and height + item[2] > 2400:
                batches.append(batch)
                batch, height = [], top + header_height
            batch.append(item)
            height += item[2]
        batches.append(batch)
        for batch in batches:
            height = top + header_height + sum(item[2] for item in batch) + 72
            canvas = Image.new('RGB', (width, height), '#f4f7fb')
            draw = ImageDraw.Draw(canvas)
            y = 32
            for line in title_lines:
                draw.text((32, y), line, font=title_font, fill='#182b49')
                y += 46
            y += 14
            for line in subtitle_lines:
                draw.text((32, y), line, font=font, fill='#4c5b70')
                y += line_height
            y = top

            def draw_row(lines, values, row_height, face, background, foreground):
                x = 32
                for i, (cell_lines, size) in enumerate(zip(lines, widths)):
                    draw.rectangle((x, y, x + size, y + row_height), fill=background, outline='#ccd5e2', width=1)
                    value = values[i] if values is not None else None
                    numeric = (isinstance(value, (int, float)) and not isinstance(value, bool)) or (
                        isinstance(value, str) and bool(re.fullmatch(r'-?\d+\.\d+', value)))
                    for line_no, line in enumerate(cell_lines):
                        tx = x + size - padding - face.getlength(line) if numeric else x + padding
                        draw.text((tx, y + padding + line_no * line_height), line, font=face, fill=foreground)
                    x += size

            draw_row(headers, None, header_height, header, '#203b61', '#ffffff')
            y += header_height
            for i, (values, lines, row_height) in enumerate(batch):
                draw_row(lines, values, row_height, font, '#ffffff' if i % 2 == 0 else '#edf3fa', '#182b49')
                y += row_height
            footer = '빈 결과 (0행)' if not rows else f'표시: {len(rows)}행'
            if len(columns) > 4:
                footer += f' · 컬럼 {offset + 1}–{offset + len(names)} / {len(columns)}'
            draw.text((32, y + 18), footer, font=font, fill='#4c5b70')
            output = BytesIO()
            canvas.save(output, format='PNG')
            pages.append(output.getvalue())
    return pages
