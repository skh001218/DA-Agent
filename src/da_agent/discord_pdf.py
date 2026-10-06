"""Public submission PDF; embedded Korean font, no model or private records."""
import hashlib
import os
import re
from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape

from .errors import DomainError

_FONT_LOCK = Lock()


def pdf_filename(submission):
    digest = hashlib.sha256((submission['session_id'] + ':' + submission['evaluation_id']).encode()).hexdigest()[:20]
    return f'analysis-{digest}.pdf'


def plain_text(text):
    return re.sub(r'\\([\\`*_{}\[\]()<>|~#])', r'\1', str(text)).replace('@\u200b', '@')


def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    paths = [os.environ.get('DISCORD_PDF_FONT', ''),
             '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
             'C:/Windows/Fonts/malgun.ttf']
    with _FONT_LOCK:
        if 'DA-Korean' not in pdfmetrics.getRegisteredFontNames():
            path = next((p for p in paths if p and Path(p).is_file()), None)
            if not path:
                raise DomainError('result_pdf_font', 'PDF 한글 폰트가 없습니다. NanumGothic TTF 또는 DISCORD_PDF_FONT를 준비한 뒤 /resume하세요.')
            pdfmetrics.registerFont(TTFont('DA-Korean', path))
            bold_paths = [str(Path(path).with_name('NanumGothicBold.ttf')),
                          str(Path(path).with_name('malgunbd.ttf'))]
            bold_path = next((p for p in bold_paths if Path(p).is_file()), path)
            pdfmetrics.registerFont(TTFont('DA-Korean-Bold', bold_path))
    return 'DA-Korean'


def render_submission_pdf(submission):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether, PageBreak
    from .discord_pdf_evidence import display_reference_text, evidence_flowables, PDFEvidenceReferences
    font = _font()
    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=48, leftMargin=48,
                            topMargin=55, bottomMargin=55, title=submission['post_name'],
                            author='DA-Agent', pageCompression=1)
    heading = ParagraphStyle('section', fontName='DA-Korean-Bold', fontSize=13, leading=21,
                             textColor=colors.HexColor('#315d91'), spaceBefore=20,
                             spaceAfter=10, wordWrap='CJK', keepWithNext=True)
    body = ParagraphStyle('body', fontName=font, fontSize=11, leading=18,
                          textColor=colors.HexColor('#192A3D'),
                          spaceAfter=7, wordWrap='CJK', splitLongWords=True)
    title = ParagraphStyle('title', parent=heading, fontSize=22, leading=33,
                           textColor=colors.HexColor('#192A3D'), spaceBefore=0, spaceAfter=18)
    caption = ParagraphStyle('caption', parent=body, fontSize=9, leading=15,
                             textColor=colors.HexColor('#526275'))
    label = ParagraphStyle('label', parent=body, fontName='DA-Korean-Bold',
                           textColor=colors.HexColor('#315d91'), keepWithNext=True)
    cards = submission['cards']
    report_title = plain_text(cards[0]['description']).split('\n')[0] if cards else submission['post_name']
    sql_practice = submission.get('practice') == 'sql'
    document_label = 'DA / SQL PRACTICE' if sql_practice else 'DA / ANALYSIS REPORT'
    story = [Paragraph(document_label, caption), Spacer(1, 6),
             Paragraph(escape(report_title), title)]
    evidence_results = submission.get('evidence_results', [])
    references = PDFEvidenceReferences(submission)
    report_evidence = [(i, e) for i, e in enumerate(evidence_results, 1)
                       if e.get('placement', 'report') == 'report']
    if report_evidence:
        metadata = next((line for line in plain_text(cards[0]['description']).split('\n')
                         if line.startswith('보고 버전:')), '')
        if metadata:
            story.append(Paragraph(escape(metadata), caption))
        if any('합성' in card['description'] for card in cards):
            story.append(Paragraph('데이터: 합성 훈련 데이터의 실제 저장 조회 결과', caption))
        for number, evidence in report_evidence:
            visual = evidence_flowables(evidence, number, font, heading, caption)
            story.extend(visual)
            references.mark_query_drawn(evidence['execution_id'], visual)
        story.append(PageBreak())
    for index, card in enumerate(cards):
        section = []
        anchors = ''
        if index == 0:
            anchors += '<a name="submission-summary"/>'
        if card.get('pdf_kind') == 'report' and not any(c.get('pdf_kind') == 'report' for c in cards[:index]):
            anchors += '<a name="submitted-report"/>'
        if card.get('pdf_kind') == 'followup' and not any(c.get('pdf_kind') == 'followup' for c in cards[:index]):
            anchors += '<a name="followup-answers"/>'
        section.append(Paragraph(anchors + escape(f'{index + 1:02d}  ' + plain_text(card['title'])), heading))
        description = display_reference_text(plain_text(card.get('pdf_description', card['description'])), evidence_results)
        if card['title'].startswith('제출 보고서'):
            # Only add paragraph breaks at existing labels; never rewrite the report.
            description = re.sub(r'(?<!^)(?<!\n)(?=(?:분석 질문|발견한 사실|가설|대안 설명|데이터 신뢰성|한계|우선 대응|후속 검증):)', '\n\n', description)
        for line in description.split('\n'):
            is_label = line in ('평가 근거', '다음 개선 행동', '인용한 근거') or re.fullmatch(r'답변 \d+', line)
            style = label if is_label else body
            section.append(Paragraph(escape(line), style) if line else Spacer(1, 6))
        if card.get('source_url'):
            section.append(Paragraph(escape('원래 과제 공간: ' + card['source_url']), caption))
        if index == 0:
            summary = Table([[section]], colWidths=[A4[0] - 96], splitInRow=1)
            summary.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#EDF3FA')),
                ('LEFTPADDING', (0, 0), (-1, -1), 16),
                ('RIGHTPADDING', (0, 0), (-1, -1), 16),
                ('TOPPADDING', (0, 0), (-1, -1), 12),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ]))
            story.extend([summary, Spacer(1, 6)])
        else:
            if not card['title'].startswith('제출 보고서'):
                story.append(KeepTogether(section))
            else:
                story.extend(section)
        story.extend(references.flowables(card.get('pdf_evidence_refs', []), font, heading, caption, body))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#DCE3EA'))
        canvas.setLineWidth(.6)
        canvas.line(48, 40, A4[0] - 48, 40)
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor('#526275'))
        canvas.drawString(48, 26, 'DA-Result | SQL 연습 결과' if sql_practice else 'DA-Result | 분석 보고서 · 표준 보고서형')
        canvas.drawRightString(A4[0] - 48, 26, str(document.page))
        if document.page > 1:
            canvas.drawString(48, A4[1] - 30, document_label)
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
