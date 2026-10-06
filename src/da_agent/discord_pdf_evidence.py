"""PDF evidence uses saved result rows, not narrative numbers or regenerated SQL."""
import math
import re
from io import BytesIO
from xml.sax.saxutils import escape


def display_reference_text(text, evidence_results):
    """Replace technical locators with inspectable figures; retain surrounding claims."""
    for number, evidence in enumerate(evidence_results, 1):
        ident = re.escape(str(evidence['execution_id']))
        text = re.sub(r'(?:근거 조회 ID|저장 조회 ID|실행 ID|조회 ID):\s*' + ident,
                      f'근거: 조회 결과 {number}', text)
        text = re.sub(ident, f'조회 결과 {number}', text)
    uuid = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
    text = re.sub(r'(?:근거 조회 ID|저장 조회 ID|실행 ID|조회 ID):\s*' + uuid,
                  '근거 조회: 저장 결과 연결을 확인할 수 없음', text)
    text = re.sub(r'과제 대화 ID:\s*' + uuid, '근거: 과제 대화 (원래 과제 공간에서 확인)', text)
    return text


def evidence_flowables(evidence, number, font, heading, caption):
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Drawing, Rect, String, Line
    from reportlab.platypus import Paragraph, Image, Spacer, Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle
    from .discord_tables import render_table_png, cell_text

    conditions = evidence.get('conditions', {})
    names = [col['name'] if isinstance(col, dict) else str(col) for col in evidence['columns']]
    rows = evidence['rows']
    tutorial = conditions.get('metric') == 'tutorial_rate'
    metric_title = '튜토리얼 완료율'
    if tutorial and conditions.get('step') is not None:
        metric_title = f"튜토리얼 {conditions['step']}단계 완료율"
    title = f"조회 결과 {number} · " + (metric_title if tutorial else '선택한 분석 근거')
    scope = []
    if conditions.get('start') and conditions.get('end'):
        scope.append(f"가입 기간: {conditions['start']} 이상, {conditions['end']} 미만")
    if conditions.get('event_end'):
        scope.append(f"완료 관찰: {conditions['event_end']} 미만")
    if conditions.get('timezone'):
        scope.append('시간 기준: ' + conditions['timezone'])
    content = [Paragraph(f'<a name="query-result-{number}"/>' + escape(title), heading)]
    if scope:
        content.append(Paragraph(escape(' · '.join(scope)), caption))

    chart_rows = []
    if 'group_value' in names and 'rate_percent' in names and len(rows) <= 8:
        group_index, rate_index = names.index('group_value'), names.index('rate_percent')
        for row in rows:
            if row[rate_index] is None:
                continue
            try:
                value = float(row[rate_index])
            except (ValueError, TypeError):
                continue
            if math.isfinite(value) and 0 <= value <= 100:
                chart_rows.append((str(row[group_index]), value))
    if chart_rows:
        height = 54 + len(chart_rows) * 38
        drawing = Drawing(499, height)
        left, plot_width = 125, 300
        for tick in [0, 25, 50, 75, 100]:
            x = left + plot_width * tick / 100
            drawing.add(Line(x, 26, x, height-12, strokeColor=colors.HexColor('#DCE3EA'), strokeWidth=.5))
            drawing.add(String(x, 10, f'{tick}%', fontName=font, fontSize=9,
                               textAnchor='middle', fillColor=colors.HexColor('#526275')))
        for index, (group, value) in enumerate(chart_rows):
            y = height - 34 - index*38
            # Full category labels are wrapped in the adjacent table if too wide.
            label = group if len(group) <= 16 else group[:15] + '…'
            drawing.add(String(left-12, y+6, label, fontName=font, fontSize=10,
                               textAnchor='end', fillColor=colors.HexColor('#192A3D')))
            drawing.add(Rect(left, y, plot_width*value/100, 22,
                             fillColor=colors.HexColor('#315D91'), strokeColor=None))
            drawing.add(String(439, y+6, f'{value:.2f}%', fontName='DA-Korean-Bold', fontSize=11,
                               fillColor=colors.HexColor('#315D91')))
        content.extend([Spacer(1, 10), drawing, Spacer(1, 12)])
        if len(chart_rows) != len(rows):
            content.append(Paragraph('값이 없거나 유효한 백분율이 아닌 행은 그래프에서 제외했습니다. 표에서 확인하세요.', caption))

    labels = {'group_value': '구분', 'denominator': '분모', 'numerator': '분자', 'rate_percent': '비율 (%)'}
    if tutorial and conditions.get('denominator') == 'signup_users':
        labels['denominator'] = '가입자 (명)'
    if tutorial and conditions.get('numerator') == 'completed_users':
        labels['numerator'] = '완료자 (명)'
        labels['rate_percent'] = '완료율 (%)'
    formatted = []
    for row in rows:
        values = []
        for name, value in zip(names, row):
            if name == 'rate_percent' and value is not None:
                try:
                    value = f'{float(value):.2f}'
                except (ValueError, TypeError):
                    pass
            values.append(value if isinstance(value, int) and not isinstance(value, bool) else cell_text(value))
        formatted.append(values)
    note = f'저장된 결과 {len(rows)}행'
    if evidence.get('truncated'):
        note += f" · 일부 결과만 저장됨 (전체 {evidence.get('total_row_count') or '미상'}행)"
    elif 'rate_percent' in names:
        note += ' · 비율은 소수 둘째 자리까지 표시'
    # Reuse the Discord image renderer, with PDF-readable headings and no IDs.
    for start in range(0, max(1, len(formatted)), 12):
        for offset in range(0, max(1, len(names)), 4):
            panel_names = names[offset:offset+4]
            panel_rows = [row[offset:offset+4] for row in formatted[start:start+12]]
            table = dict(title='조회 결과표', subtitle=note,
                         columns=[labels.get(name, name) for name in panel_names], rows=panel_rows)
            images = [Image(BytesIO(png)) for png in render_table_png(table)]
            if any(image.imageHeight * 499/image.imageWidth > 520 for image in images):
                # Keep long cells legible instead of shrinking an entire tall bitmap.
                cell_style = ParagraphStyle('result-cell', fontName=font, fontSize=10,
                                            leading=16, wordWrap='CJK')
                head_style = ParagraphStyle('result-head', parent=cell_style,
                                            fontName='DA-Korean-Bold', textColor=colors.white)
                values = [[Paragraph(escape(col), head_style) for col in table['columns']]]
                values.extend([[Paragraph(escape(str(cell)), cell_style) for cell in row] for row in panel_rows])
                native = Table(values, colWidths=[499/max(1, len(panel_names))]*max(1, len(panel_names)),
                               repeatRows=1, splitInRow=1)
                native.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#203B61')),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#EDF3FA')]),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
                    ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
                ]))
                content.extend([Paragraph('조회 결과표 · ' + escape(note), caption), native, Spacer(1, 10)])
            else:
                for image in images:
                    image.drawWidth = 499
                    image.drawHeight = image.imageHeight * 499/image.imageWidth
                    content.extend([image, Spacer(1, 10)])
    if tutorial and conditions.get('numerator') == 'completed_users' and conditions.get('denominator') == 'signup_users':
        content.append(Paragraph('완료율 = 완료한 고유 사용자 ÷ 가입한 고유 사용자 × 100. 관측 차이만으로 원인을 확정할 수 없습니다.', caption))
    return content


class PDFEvidenceReferences:
    """Resolve citations to previously drawn figures or show new evidence once."""

    def __init__(self, submission):
        self.submission = submission
        self.queries = {e['execution_id']: (i, e)
                        for i, e in enumerate(submission.get('evidence_results', []), 1)}
        self.messages = {m['id']: (i, m) for i, m in enumerate(submission.get('message_sources', []), 1)}
        self.drawn_queries = set()
        self.query_visual_labels = {}
        self.drawn_messages = set()

    def mark_query_drawn(self, ident, flowables):
        from reportlab.graphics.shapes import Drawing
        self.drawn_queries.add(ident)
        self.query_visual_labels[ident] = '그래프·표' if any(isinstance(f, Drawing) for f in flowables) else '표'

    def flowables(self, refs, font, heading, caption, body):
        from reportlab.platypus import Paragraph, Table, TableStyle, Spacer
        from reportlab.lib import colors
        output = []
        if not refs:
            return output
        output.append(Paragraph('인용한 근거', heading))

        def link(target, label):
            output.append(Paragraph(f'<link href="#{target}" color="#315D91">{escape(label)}</link>', body))

        for ref in dict.fromkeys(refs):
            kind, _, ident = ref.partition(':')
            if kind == 'execution':
                source = self.queries.get(ident)
                if source is None:
                    output.append(Paragraph('인용한 조회 결과를 확인할 수 없습니다. 저장 근거 연결을 확인하세요.', caption))
                    continue
                number, evidence = source
                if ident in self.drawn_queries:
                    link(f'query-result-{number}', f'앞의 조회 결과 {number} {self.query_visual_labels[ident]} 참조')
                else:
                    visual = evidence_flowables(evidence, number, font, heading, caption)
                    output.extend(visual)
                    self.mark_query_drawn(ident, visual)
            elif kind == 'report':
                if (str(self.submission.get('report_version')) == ident
                        and any(c.get('pdf_kind') == 'report' for c in self.submission['cards'])):
                    link('submitted-report', f'앞의 제출 보고서 버전 {ident} 참조')
                else:
                    output.append(Paragraph('인용한 보고서 버전을 확인할 수 없습니다.', caption))
            elif kind == 'message':
                source = self.messages.get(ident)
                if source is None:
                    output.append(Paragraph('인용한 과제 대화를 확인할 수 없습니다. 원래 과제 공간에서 확인하세요.', caption))
                    continue
                number, message = source
                if message['location'] == 'summary':
                    link('submission-summary', '앞의 제출 결과 요약 · 업무 목표 참조')
                elif message['location'] == 'followup':
                    link('followup-answers', f"앞의 후속 질문 답변 {message['answer_number']} 참조")
                elif ident in self.drawn_messages:
                    link(f'quoted-message-{number}', f'앞의 과제 대화 근거 {number} 참조')
                else:
                    output.append(Paragraph(f'<a name="quoted-message-{number}"/>과제 대화 근거 {number} · 실제 인용', caption))
                    paragraphs = [Paragraph(escape(line), body) for line in message['text'].split('\n') if line.strip()]
                    quote = Table([[paragraphs]], colWidths=[499], splitInRow=1)
                    quote.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F1F4F8')),
                        ('LINEBEFORE', (0, 0), (-1, -1), 2, colors.HexColor('#315D91')),
                        ('LEFTPADDING', (0, 0), (-1, -1), 14), ('RIGHTPADDING', (0, 0), (-1, -1), 14),
                        ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                    ]))
                    output.extend([quote, Spacer(1, 8)])
                    self.drawn_messages.add(ident)
            else:
                output.append(Paragraph('지원하지 않는 인용 근거 형식입니다. 원래 과제 공간에서 확인하세요.', caption))
        return output
