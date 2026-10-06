"""Five comparable A4 design previews using the verified Discord test data."""
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/pdf/layout-options-2026-10-06'
OUT.mkdir(parents=True, exist_ok=True)
pdfmetrics.registerFont(TTFont('KR', 'C:/Windows/Fonts/malgun.ttf'))
pdfmetrics.registerFont(TTFont('KRB', 'C:/Windows/Fonts/malgunbd.ttf'))
W, H = 595.276, 841.89
INK = '#192A3D'
MUTED = '#526275'
SOURCE = 'a5373a79-59c7-4baf-8777-045c83b2b8ef'
LINK = 'https://discord.com/channels/1556888486919934064/1556931093222002710'
path = OUT / 'five-pdf-layout-options.pdf'
c = canvas.Canvas(str(path), pagesize=(W, H))
c.setTitle('DA-Agent 분석 PDF 형식 추천안 5종')
c.setAuthor('DA-Agent')


def text(x, top, content, size=11, color=INK, bold=False, width=499, leading=None):
    p = Paragraph(content, ParagraphStyle('p', fontName='KRB' if bold else 'KR',
                  fontSize=size, leading=leading or size * 1.6,
                  textColor=HexColor(color), wordWrap='CJK'))
    _, height = p.wrap(width, 900)
    assert top + height < 795, (content, top, height)
    p.drawOn(c, x, H - top - height)
    return top + height


def box(x, top, width, height, fill, border=None, radius=0):
    c.setFillColor(HexColor(fill))
    c.setStrokeColor(HexColor(border or fill))
    c.roundRect(x, H - top - height, width, height, radius, fill=1,
                stroke=bool(border))


def line(top, x=48, width=499, color='#DCE3EA'):
    c.setStrokeColor(HexColor(color))
    c.setLineWidth(.65)
    c.line(x, H-top, x+width, H-top)


def header(number, name, accent, subtitle):
    text(48, 31, f'DA / ANALYSIS REPORT     {number:02d} · {name}', 9, accent, True)
    text(48, 65, '유입 채널별 튜토리얼 완료율', 24, INK, True)
    text(48, 107, subtitle, 10, MUTED)
    text(48, 132, '가입 2026.09.08~09.21 UTC  |  완료 관찰 2026.09.30까지  |  v1', 9, MUTED)


def footer(number, name):
    line(790)
    c.setFont('KR', 8)
    c.setFillColor(HexColor(MUTED))
    c.drawString(48, 34, f'DA-Agent · {name} · 형식 비교용 시안 / 합성 데이터')
    c.drawRightString(W-48, 34, f'{number} / 5')
    c.showPage()


def table(top, accent, x=48, width=499, height=36):
    cols = [0, width*.36, width*.56, width*.78]
    box(x, top, width, 30, accent)
    for offset, label in zip(cols, ['채널', '가입자', '완료자', '완료율']):
        text(x+offset+12, top+7, label, 9, '#FFFFFF', True, width=width*.2)
    for row, values in enumerate([['ads', '19명', '13명', '68.42%'],
                                   ['organic', '21명', '17명', '80.95%'],
                                   ['전체', '40명', '30명', '75.00%']]):
        y = top+30+row*height
        box(x, y, width, height, '#F1F4F7' if row%2==0 else '#FFFFFF')
        for offset, value in zip(cols, values):
            text(x+offset+12, y+8, value, 10, INK, row==2, width=width*.2)
    return top+30+3*height


def source(top, link_color='#315D91'):
    text(48, top, '출처 · 검증용 합성 데이터 / 성공한 DB 조회', 8, MUTED)
    text(48, top+16, f'조회 ID: {SOURCE}', 8, MUTED)
    text(48, top+32, '<link href="'+LINK+'" color="'+link_color+'">원본 포럼 게시글 열기</link>', 8)


# 1: General purpose, clear hierarchy, sufficient room for long analysis.
header(1, '표준 보고서형', '#315D91', '추천 기본형 · 결론 → 수치 → 해석 → 다음 행동')
box(48, 175, 499, 90, '#EDF3FA', radius=5)
text(64, 188, '핵심 결론', 10, '#315D91', True, width=467)
text(64, 211, 'ads의 완료율은 organic보다 12.53%p 낮습니다.', 14, INK, True, width=467)
text(64, 238, '우선 이탈 구간을 확인한 뒤 개선 실험의 범위를 정합니다.', 10, MUTED, width=467)
text(48, 286, '01  관찰 결과', 12, '#315D91', True)
table(315, '#315D91')
text(48, 467, '02  해석과 한계', 12, '#315D91', True)
text(48, 498, '채널별 차이는 확인됐지만, 유입 채널이 원인이라고 단정할 수 없습니다. 가입 주차와 사용자 구성의 차이를 추가로 확인해야 합니다.')
text(48, 574, '03  다음 행동', 12, '#315D91', True)
text(48, 605, '① 단계별 이탈과 가입 주차별 완료율을 조회합니다.<br/>② ads 사용자의 이탈 구간을 확인하고 개선 실험을 설계합니다.')
source(709)
footer(1, '표준 보고서형')

# 2: Briefing with metrics and small chart.
header(2, '핵심 요약형', '#087C83', '빠른 의사결정 · 중요한 숫자와 실행 우선순위를 먼저 확인')
for x, label, value in [(48, '전체 완료율', '75.00%'), (219, 'ads 완료율', '68.42%'), (390, '채널 간 차이', '12.53%p')]:
    box(x, 178, 157, 88, '#EAF5F4', radius=6)
    text(x+13, 190, label, 9, '#087C83', True, width=131)
    text(x+13, 217, value, 23, '#07686E', True, width=140)
text(48, 290, '먼저 할 일: ads 이탈 구간 확인', 17, INK, True)
text(48, 327, 'ads의 완료율이 organic보다 낮습니다. 원인을 추정하기 전에 단계별 이탈과 가입 주차별 차이를 확인합니다.', 11)
text(48, 389, '채널별 완료율', 11, '#087C83', True)
for top, name, val in [(421, 'ads', 68.42), (459, 'organic', 80.95)]:
    text(48, top+2, name, 10, width=70)
    box(127, top, 323, 23, '#E9EFF2')
    box(127, top, 323*val/100, 23, '#087C83')
    text(460, top+1, f'{val:.2f}%', 10, '#087C83', True, width=90)
c.setFont('KR', 8)
c.setFillColor(HexColor(MUTED))
c.drawString(127, H-501, '0%')
c.drawCentredString(127+323/2, H-501, '50%')
c.drawRightString(450, H-501, '100%')
table(522, '#087C83', height=29)
text(48, 650, '판단 유보 · 채널별 표본 19명 / 21명. 인과관계는 검증 전입니다.', 10, MUTED)
source(709)
footer(2, '핵심 요약형')

# 3: Evidence trace, a compact audit trail.
header(3, '근거 중심형', '#6551A4', '재현과 검토 · 질문, 집계 기준, 결과, 해석을 연결')
text(48, 176, '01  분석 질문', 12, '#6551A4', True)
text(48, 207, '유입 채널별 튜토리얼 3단계 완료율은 얼마나 다른가?')
line(240)
text(48, 257, '02  집계 기준', 12, '#6551A4', True)
box(48, 288, 499, 89, '#F3F0FA', radius=4)
text(64, 300, '분모: 기간 내 가입한 고유 사용자<br/>분자: 관찰 종료 전 3단계를 완료한 고유 사용자<br/>중복 제거: 사용자별 완료 여부 / 완료율: 완료자 ÷ 가입자', 10, width=467)
text(48, 399, '03  확인된 결과', 12, '#6551A4', True)
table(430, '#6551A4', height=30)
text(48, 569, '04  해석과 추가 검증', 12, '#6551A4', True)
text(48, 600, 'ads 68.42%, organic 80.95%로 12.53%p 차이입니다. 이 차이만으로 채널의 인과 효과를 설명할 수 없습니다.<br/>다음 조회: 가입 주차별 완료율과 단계별 이탈 현황.', 11)
source(709)
footer(3, '근거 중심형')

# 4: Learning journal, fact/inference/action deliberately separated.
header(4, '학습 리뷰형', '#B06C27', '훈련 기록 · 관찰한 사실, 해석, 보완할 일을 구분')
table(177, '#B06C27', height=31)
panels = [
    (326, '01  확인한 사실', 'ads 13/19명(68.42%), organic 17/21명(80.95%).<br/>전체 완료율은 30/40명(75.00%)입니다.'),
    (440, '02  현재의 해석', 'ads의 완료율이 12.53%p 낮아 우선 확인할 대상입니다.<br/>유입 채널이 원인이라는 판단은 아직 근거가 부족합니다.'),
    (554, '03  보완할 분석', '가입 주차와 단계별 이탈을 나눠 확인합니다.<br/>차이를 만든 구간을 확인한 뒤 개선 실험을 설계합니다.'),
]
for top, title, body in panels:
    box(48, top, 499, 98, '#FBF5ED', radius=4)
    box(48, top, 4, 98, '#B06C27')
    text(66, top+12, title, 12, '#92541C', True, width=462)
    text(66, top+43, body, 11, width=462)
source(709)
footer(4, '학습 리뷰형')

# 5: Sparse, larger body and grayscale for printing.
header(5, '흑백 인쇄형', '#303030', '인쇄와 보관 · 색상 없이도 구분되는 제목과 표')
line(169, color='#303030')
text(48, 190, '요약', 15, '#202020', True)
text(48, 225, 'ads의 완료율은 organic보다 12.53%p 낮습니다. 단계별 이탈을 확인하고 개선 실험의 범위를 정합니다.', 12, '#202020', leading=20)
text(48, 295, '분석 결과', 15, '#202020', True)
table(334, '#303030', height=36)
text(48, 488, '해석의 한계', 15, '#202020', True)
text(48, 522, '채널별 표본은 19명과 21명입니다. 합성 데이터의 관찰 결과이며 채널이 원인이라고 단정할 수 없습니다.', 12, '#202020', leading=20)
text(48, 596, '다음 행동', 15, '#202020', True)
text(48, 630, '가입 주차별 완료율과 단계별 이탈을 추가 조회하고, 확인된 이탈 구간을 대상으로 개선 실험을 설계합니다.', 12, '#202020', leading=20)
source(719, '#303030')
footer(5, '흑백 인쇄형')
c.save()
reader = PdfReader(path)
assert len(reader.pages) == 5
for page in reader.pages:
    extracted = page.extract_text()
    assert all(v in extracted for v in ['68.42', '80.95', SOURCE])
print(f'{path}: 5 pages, Korean text and source ID verified')
