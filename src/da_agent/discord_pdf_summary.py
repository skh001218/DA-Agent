"""Three-page learning PDF from saved public submission excerpts and evidence."""
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.sax.saxutils import escape

SUMMARY_VERSION = 'v1'


def summary_filename(submission):
    from .discord_pdf import pdf_filename
    return pdf_filename(submission).replace('analysis-', f'analysis-summary-{SUMMARY_VERSION}-', 1)


def _clip(text, limit):
    text = re.sub(r'\s+', ' ', str(text)).strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _excerpt(text, pattern, limit=180, count=2):
    """Select complete original sentences; never cut away a sentence's qualifier."""
    selected = []
    for sentence in re.split(r'(?<=[.!?。])\s+|\n+', text):
        sentence = sentence.strip()
        if not re.search(pattern, sentence) or sentence in selected or not sentence:
            continue
        if len(' '.join([*selected, sentence])) <= limit:
            selected.append(sentence)
        if len(selected) >= count:
            break
    return ' '.join(selected) or '이 항목은 상세 원문 PDF에서 확인하세요.'


def _table(evidence, number, max_rows):
    """Disclosed stored-row preview. Ratio units require matching numerator/counts."""
    from .discord_tables import cell_text
    names = [c.get('name', '') if isinstance(c, dict) else str(c) for c in evidence.get('columns', [])]
    rows = evidence.get('rows', [])
    labels = {'acquisition_channel': '유입 채널', 'prior_game_experience': '게임 경험',
              'total_account_count': '대상 계정', 'completed_account_count': '완료 계정',
              'incomplete_account_count': '미완료 계정', 'group_value': '구분',
              'denominator': '분모', 'numerator': '분자', 'rate_percent': '비율 (%)'}
    ratio = bool(rows) and len(names) == len(set(names)) and {'total_account_count', 'completed_account_count', 'completion_rate'}.issubset(names)
    if ratio:
        try:
            for row in rows:
                total = Decimal(str(row[names.index('total_account_count')]))
                done = Decimal(str(row[names.index('completed_account_count')]))
                rate = Decimal(str(row[names.index('completion_rate')]))
                if not (total.is_finite() and done.is_finite() and rate.is_finite()
                        and total > 0 and 0 <= done <= total and abs(done / total - rate) < Decimal('1e-10')):
                    ratio = False
                    break
        except (InvalidOperation, IndexError, TypeError, ZeroDivisionError):
            ratio = False
    labels['completion_rate'] = '완료율 (%)' if ratio else '완료율 (원값)'
    chosen = names[:5]
    combine = ratio and {'prior_game_experience', 'acquisition_channel', 'incomplete_account_count'}.issubset(names)
    if combine:
        chosen = ['acquisition_channel', 'prior_game_experience', 'completed_account_count',
                  'completion_rate', 'incomplete_account_count']
    headers = [_clip('완료 / 대상' if combine and n == 'completed_account_count' else labels.get(n, n), 20)
               for n in chosen]
    rendered, abbreviated = [], False
    chosen_indices = [names.index(name) for name in chosen] if combine else list(range(len(chosen)))
    for row in rows[:max_rows]:
        cells = []
        for name, index in zip(chosen, chosen_indices):
            value = row[index] if index < len(row) else None
            if combine and name == 'completed_account_count':
                value = f"{cell_text(value)} / {cell_text(row[names.index('total_account_count')])}"
            elif ratio and name == 'completion_rate':
                value = format(Decimal(str(value)) * 100, '.2f').rstrip('0').rstrip('.')
            else:
                value = cell_text(value)
            abbreviated |= len(str(value)) > 28
            cells.append(_clip(value, 28))
        rendered.append(cells)
    notes = [f'조회 결과 {number} · 저장 {len(rows)}행 중 {len(rendered)}행 발췌']
    if len(chosen) < len(names) and not combine:
        notes.append(f'{len(names)}열 중 {len(chosen)}열')
    if evidence.get('truncated'):
        notes.append(f"일부 결과만 저장됨 · 전체 {evidence.get('total_row_count') or '미상'}행")
    if abbreviated or any(len(labels.get(n, n)) > 20 for n in chosen):
        notes.append('긴 열 제목·셀은 … 표시')
    if not headers:
        headers, rendered = ['조회 결과'], [['표시할 저장 열이 없습니다.']]
    return dict(headers=headers, rows=rendered, caption=' · '.join(notes))


def summary_data(submission):
    from .discord_pdf import plain_text
    from .discord_pdf_evidence import display_reference_text
    from .discord_task_brief import ISO_DATE, _kst, _source_timezone, time_label
    cards, evidence = submission['cards'], submission.get('evidence_results', [])
    def readable(text):
        return display_reference_text(plain_text(text), evidence)
    report = readable(' '.join(c['description'] for c in cards if c.get('pdf_kind') == 'report'))
    followup = readable(' '.join(c['description'] for c in cards if c.get('pdf_kind') == 'followup'))
    feedback, seen = [], set()
    for card in cards:
        base = card['title'].split(' · 이어서 ')[0]
        match = re.match(r'(.+?) · (?:모델 관측 )?등급 (\d)/4 · 비중|(.+?) · 판정 보류 · 비중', plain_text(base))
        if not match or base in seen:
            continue
        seen.add(base)
        text = readable(''.join(c.get('pdf_description', c['description']) for c in cards
                                if c['title'].split(' · 이어서 ')[0] == base))
        action = text.partition('다음 개선 행동')[2].partition('인용한 근거')[0].strip()
        feedback.append([_clip(match[1] or match[3], 24), f'{match[2]} / 4' if match[2] else '보류',
                         _clip(action or '보류 이유 확인 후 재평가', 65)])
    feedback_note = f'모델 평가 {len(feedback)}항목 중 {min(5, len(feedback))}항목 발췌 · 긴 문장은 … 표시'
    if not feedback:
        feedback = [['평가', '보류', '상세 원문의 평가 상태를 확인하세요.']]
    first = readable(cards[0]['description']).splitlines() if cards else []
    context = submission.get('summary_context', {})
    period = context.get('period', {})
    scope = (period.get('description') or ' / '.join(str(period[k]) for k in ('start', 'end') if period.get(k))
             or '상세 원문의 관측 조건 확인')
    source_zone = _source_timezone(context, scope)
    def short_date(match):
        try:
            return time_label(_kst(match.group(), source_zone), date_only=len(match.group()) == 10)
        except ValueError:
            return match.group()
    scope = ISO_DATE.sub(short_date, scope)
    meta = [_clip('공개 관측 기간: ' + scope, 100),
            f"보고 버전 {submission.get('report_version', '미상')} · 저장 조회 {len(evidence)}개 · 제출 보고 발췌"]
    if context.get('timezone'):
        meta[0] = _clip(meta[0] + ' · ' + ('KST' if context['timezone'] == 'Asia/Seoul' else context['timezone']), 110)
    blank = dict(headers=['저장 조회 근거'], rows=[['선택한 저장 조회 결과가 없습니다.']],
                 caption='표시할 조회 결과 없음 · 보고서 원문은 상세 PDF에서 확인하세요.')
    metrics = _table(evidence[0], 1, 3) if evidence else blank
    detail_number = 2 if len(evidence) > 1 else 1
    details = _table(evidence[detail_number - 1], detail_number, 4) if evidence else blank
    conditions = evidence[detail_number - 1].get('conditions', {}) if evidence else {}
    condition_text = ' · '.join(f'{k}: {v}' for k, v in conditions.items())
    status = [first[1] if len(first) > 1 else '평가 상태는 상세 원문에서 확인하세요.']
    for title in ('평가 신뢰성 확인', '실행 근거 검산'):
        card = next((c for c in cards if c['title'] == title), None)
        if card:
            value = readable(card['description']).splitlines()[0]
            status.append({'unverified': '자연어 수치 자동 검산 미실시', 'not_checked': '자동 검산 근거 부족'}.get(value, value))
    status = [_clip(' · '.join(status), 210), '모델 관측이며 확정 학습 효과·사람 검토 완료를 뜻하지 않습니다.']
    url = submission.get('result_url') or f"https://discord.com/channels/{submission['guild_id']}"
    insight = _excerpt(report, r'관계|구성|반대|일관', 120)
    if insight == '이 항목은 상세 원문 PDF에서 확인하세요.':
        insight = _excerpt(report, r'비교|차이|가설', 120)
    return dict(
        title=_clip(context.get('title') or (first[0] if first else submission['post_name']), 75), meta=meta,
        conclusion=_excerpt(report, r'[%％]|낮|높|차이|관계|발견', 230), metrics=metrics,
        priorities=[dict(title='우선 대응 발췌', text=_excerpt(report, r'먼저|별도|조사하고|조사한다|대응', 140, 1)),
                    dict(title='후속 검증 발췌', text=_excerpt(followup or report, r'검증|확인|실험|조사', 140))],
        caveat=_excerpt(report, r'한계|합성|인과|확정|유보|불완전|구분할 수 없', 175),
        details=details, details_title='추가 조회 근거' if len(evidence) > 1 else '저장 조회 근거',
        evidence_insight=insight,
        evidence_notes=[dict(title='분모·단위·관측 조건 발췌', text=_excerpt(report, r'분모|분자|단위|미시작', 120)),
                        dict(title='데이터 점검 발췌', text=_excerpt(report, r'중복|누락|고유|무결성', 120, 1)),
                        dict(title='조회 조건', text=_clip(condition_text, 140) if condition_text else '별도 저장된 조회 조건 없음. 제출 보고 원문에서 조건을 확인하세요.')],
        feedback=dict(headers=['평가 항목', '모델 관측', '다음 개선 행동'], rows=feedback[:5], caption=feedback_note),
        next_step=_excerpt(followup or report, r'검증|확인|실험|유보', 180), evaluation_status=status,
        source_url=url, source_note='보고 문장은 원문에서 고른 발췌입니다. 전체 표·보고서·평가 인용은 상세 원문 PDF에 있습니다.', compact_notes=True,
    )


def render_summary_pdf(submission):
    data = summary_data(submission)
    try:
        return render_learning_pdf(data)
    except ValueError as exc:
        if '3쪽 분량을 초과' not in str(exc):
            raise
    # Keep the font readable. Disclose a shorter preview if wide/tall input
    # exceeds the fixed learning pages; the detail attachment remains complete.
    for key in ('conclusion', 'caveat', 'evidence_insight', 'next_step'):
        data[key] = _excerpt(data[key], '.', 100, 1)
    for item in data['priorities'] + data['evidence_notes']:
        item['text'] = _excerpt(item['text'], '.', 90, 1)
    evidence = submission.get('evidence_results', [])
    if evidence:
        data['metrics'] = _table(evidence[0], 1, 1)
        number = 2 if len(evidence) > 1 else 1
        data['details'] = _table(evidence[number - 1], number, 1)
    for key in ('metrics', 'details'):
        data[key]['headers'] = [_clip(v, 12) for v in data[key]['headers']]
        data[key]['rows'] = [[_clip(v, 16) for v in row] for row in data[key]['rows']]
        data[key]['caption'] += ' · 분량 제한으로 긴 셀·열 제목은 … 표시'
    data['feedback']['rows'] = [[_clip(row[0], 12), row[1], _clip(row[2], 30)] for row in data['feedback']['rows']]
    data['source_note'] = '분량 제한으로 발췌를 더 줄였습니다. 전체 표·문장·평가 근거는 상세 원문 PDF에 있습니다.'
    return render_learning_pdf(data)


def render_learning_pdf(data, *, preview=False):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (
        KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )
    from reportlab.platypus.doctemplate import LayoutError
    from .discord_pdf import _font

    font = _font()
    navy, teal = colors.HexColor('#193449'), colors.HexColor('#147D83')
    muted, pale = colors.HexColor('#5E7180'), colors.HexColor('#EDF6F5')
    width = A4[0] - 108  # SimpleDocTemplate frame includes 6pt padding on each side.
    body = ParagraphStyle('body', fontName=font, fontSize=11, leading=17,
                          textColor=navy, wordWrap='CJK', spaceAfter=5)
    table_body = ParagraphStyle('cell', parent=body, fontSize=10, leading=15, spaceAfter=0)
    caption = ParagraphStyle('caption', parent=body, fontSize=9, leading=14, textColor=muted)
    head = ParagraphStyle('head', parent=body, fontName='DA-Korean-Bold',
                          fontSize=13, leading=20, spaceBefore=14, spaceAfter=7,
                          textColor=teal, keepWithNext=True)
    note_head = ParagraphStyle('note_head', parent=head, spaceBefore=6, spaceAfter=4)
    title = ParagraphStyle('title', parent=body, fontName='DA-Korean-Bold',
                           fontSize=21, leading=30, spaceAfter=14)
    table_head = ParagraphStyle('table_head', parent=table_body,
                                fontName='DA-Korean-Bold', textColor=colors.white)

    def p(text, style=body):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), style)

    def box(text, background=pale):
        table = Table([[p(text)]], colWidths=[width])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), background),
            ('LEFTPADDING', (0, 0), (-1, -1), 14), ('RIGHTPADDING', (0, 0), (-1, -1), 14),
            ('TOPPADDING', (0, 0), (-1, -1), 11), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        return table

    def grid(spec, fractions):
        headers, rows = spec['headers'], spec['rows']
        if len(headers) != len(fractions) or any(len(row) != len(headers) for row in rows):
            raise ValueError('표의 열 제목과 행의 열 수가 일치해야 합니다.')
        cells = [[p(h, table_head) for h in headers]]
        cells.extend([[p(cell, table_body) for cell in row] for row in rows])
        table = Table(cells, colWidths=[width * f for f in fractions], repeatRows=1,
                      hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), navy),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F2F6F8')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('LINEBELOW', (0, 0), (-1, 0), 1, teal),
        ]))
        return [table, Spacer(1, 6), p(spec['caption'], caption)]

    story = [p('DA / RESULT SUMMARY   ·   ' + ('편집 시안' if preview else '보고 발췌'), caption), Spacer(1, 10),
             p(data['title'], title)]
    story.extend(p(line, caption) for line in data['meta'])
    story.extend([p('핵심 결론' if preview else '제출 보고 핵심 발췌', head), box(data['conclusion']), p('한눈에 보는 수치', head)])
    story.extend(grid(data['metrics'], [.24, .18, .18, .17, .23] if len(data['metrics']['headers']) == 5 else [1 / len(data['metrics']['headers'])] * len(data['metrics']['headers'])))
    story.append(p('우선 행동', head))
    for i, item in enumerate(data['priorities'], 1):
        story.append(KeepTogether([p(f"{i:02d}  {item['title']}", head), p(item['text'])]))
    story.extend([p('해석할 때 기억할 점', head), p(data['caveat']), PageBreak(),
                  p('02 / ANALYSIS EVIDENCE', caption), Spacer(1, 10),
                  p('분석 근거', title), p(data.get('details_title', '게임 경험별 비교'), head)])
    story.extend(grid(data['details'], [.24, .18, .22, .18, .18] if len(data['details']['headers']) == 5 else [1 / len(data['details']['headers'])] * len(data['details']['headers'])))
    story.extend([p('이 표에서 읽을 것', head), box(data['evidence_insight']),
                  p('비교 조건과 데이터 점검', head)])
    for item in data['evidence_notes']:
        story.extend([p(item['title'], note_head if data.get('compact_notes') else head), p(item['text'])])
    story.extend([p('판단의 한계', head), p(data['caveat']), PageBreak(),
                  p('03 / LEARNING FEEDBACK', caption), Spacer(1, 10),
                  p('평가 피드백', title)])
    story.extend(grid(data['feedback'], [.24, .17, .59]))
    story.extend([p('다음 확인', head), p(data['next_step']), p('평가 상태', head),
                  box('\n'.join(data['evaluation_status'])), Spacer(1, 9)])
    url = data['source_url']
    if not url.startswith('https://discord.com/channels/'):
        raise ValueError('원문 링크는 Discord 결과 게시글의 HTTPS 주소여야 합니다.')
    story.append(Paragraph(f'<link href="{escape(url, {chr(34): "&quot;"})}" color="#147D83">'
                           '결과 게시판과 상세 원문 보기 →</link>', caption))
    story.append(p(data['source_note'], caption))

    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=48, rightMargin=48,
                            topMargin=43, bottomMargin=52, title=data['title'].replace('\n', ' '),
                            author='DA-Agent', pageCompression=1)

    pages = []

    def footer(canvas, document):
        pages.append(document.page)
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#D9E3E8'))
        canvas.line(48, 38, A4[0] - 48, 38)
        canvas.setFillColor(muted)
        canvas.setFont(font, 8)
        canvas.drawString(48, 24, 'DA-Result | 3쪽 학습형 · ' + ('합성 자료 편집 시안' if preview else '제출 보고 발췌'))
        canvas.drawRightString(A4[0] - 48, 24, f'{document.page} / 3')
        canvas.restoreState()

    try:
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
    except LayoutError as exc:
        raise ValueError('3쪽 분량을 초과했습니다. 긴 문단이나 표를 편집하세요.') from exc
    result = stream.getvalue()
    if pages != [1, 2, 3]:
        raise ValueError('3쪽 분량을 초과했습니다. 내용을 편집하거나 더 긴 템플릿을 선택하세요.')
    return result

