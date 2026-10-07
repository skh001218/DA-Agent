"""Local template experiment using saved public submissions, never publishing.

Excerpts are explicitly picked for layout review; this is not an automatic
semantic summarizer. Full PDF input and original public cards stay unchanged.
"""
from copy import deepcopy
from html import escape
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

import discord
from pypdf import PdfReader
from da_agent.discord_forum import ResultForumPublisher, MAX_EMBED_BYTES, embed_bytes
from da_agent.discord_pdf import plain_text, render_submission_pdf
from da_agent.discord_results import card_marker
from da_agent.discord_transport import safe_chunks

OUT = ROOT / 'tests/artifacts/discord-result-summary-2026-10-07'


def clip(value, limit):
    text = re.sub(r'\s+', ' ', plain_text(value)).strip()
    # Escape before clipping: Discord markup must never consume the budget.
    text = safe_chunks(text, limit=100000)[0]
    if len(text) <= limit:
        return text
    end = limit - 1
    if (len(text[:end]) - len(text[:end].rstrip('\\'))) % 2:
        end -= 1
    return text[:end].rstrip() + '…'


def preview_embed(submission, picked):
    """Render hand-picked public excerpts with stored score/hold priority."""
    first = submission['cards'][0]
    held = bool(first.get('held'))
    if held:
        assessment = '평가 보류 · 점수 미표시'
    elif submission.get('practice') == 'sql':
        criteria = [c for c in submission['cards'][1:]
                    if c['title'].endswith((' · 충족', ' · 보완 필요', ' · 판정 보류'))]
        passed = sum(c['title'].endswith(' · 충족') for c in criteria)
        assessment = f'{passed}/{len(criteria)}개 충족 · 준비된 검증 데이터 기준'
    elif first.get('score') is not None:
        assessment = f"{first['score']:g}/100 · 모델 평가 · 사람 검토 대기"
    else:
        assessment = '점수 미확정 · 사람 검토 대기'

    body = ['**결론**', clip(picked.get('conclusion') or '요약할 결론 미선택 · PDF 확인', 120),
            '', '**핵심 근거**']
    evidence = picked.get('evidence', [])[:3]
    body += ['• ' + clip(item, 90) for item in evidence] or ['• 선택된 근거 없음 · PDF 확인']
    body += ['', '**평가**', assessment]
    if held:
        body.append('보류 이유: ' + clip(picked.get('hold_reason') or '상세 PDF 확인', 90))
    else:
        if picked.get('strength'):
            body.append('잘한 점: ' + clip(picked['strength'], 90))
        if picked.get('improvement'):
            body.append('개선점: ' + clip(picked['improvement'], 90))
    if picked.get('warning'):
        body.append('⚠ ' + clip(picked['warning'], 90))
    body += ['', '**다음 행동**', clip(picked.get('next_action') or '상세 평가 PDF 확인', 120)]
    embed = discord.Embed(title=clip(picked['title'], 160), description='\n'.join(body),
                          colour=0xe3a23b if held or picked.get('warning') else 0x315d91)
    embed.set_author(name='참가자 · 로컬 확인본')
    embed.set_footer(text=card_marker(submission, 0))
    assert len(embed.description) <= 900
    assert len(embed.description) <= 4096
    assert embed_bytes([embed]) <= MAX_EMBED_BYTES
    return embed


def load_public(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))


def labelled(text, label):
    # Review fixtures use explicit report labels; missing labels must fail.
    value = text.split(label + ': ', 1)[1]
    return value.split('. ', 1)[0].rstrip('.') + '.'


def samples():
    analysis_path = 'tests/artifacts/discord-pdf-citations-2026-10-06/public-submission.json'
    analysis = load_public(analysis_path)
    report = plain_text(analysis['cards'][1]['description'])
    facts = labelled(report, '발견한 사실').removeprefix('발견한 사실: ').rstrip('.')
    reason = plain_text(analysis['cards'][3]['description']).split('평가 근거\n', 1)[1].split('\n', 1)[0].split('. ', 1)[1]
    improvement = plain_text(analysis['cards'][3]['description']).split('다음 개선 행동\n', 1)[1].split('\n', 1)[0].split('. ', 1)[1]
    picked = dict(title=plain_text(analysis['cards'][0]['description']).split('\n')[0],
                  conclusion=labelled(report, '한계'), evidence=facts.split(', '),
                  strength=reason, improvement=improvement, next_action=labelled(report, '우선 대응'))
    # Require each hand-picked excerpt to originate in this saved public report.
    public = '\n'.join(plain_text(c['description']) for c in analysis['cards'])
    assert all(value in public for value in [picked['conclusion'], *picked['evidence'],
               picked['strength'], picked['improvement'], picked['next_action']])
    yield 'analysis', '분석 결과', '저장된 공개 보고서 · 합성 훈련 데이터', analysis_path, analysis, picked

    sql_path = 'tests/artifacts/spec035-2026-10-06/public-submission.json'
    sql = load_public(sql_path)
    sql_picked = dict(title=plain_text(sql['cards'][0]['description']).split('\n')[0],
        conclusion='준비된 검증 데이터에서 구문·실행, 계산·출력, 중복 처리, 기간·관측, 분모·누락이 충족됐습니다.',
        evidence=['전체 실행 결과 4행 · 가입 주차와 유입 채널별 완료율',
                  '1주차 ads: 4/5 = 80% · organic: 12/15 = 80%',
                  '2주차 ads: 10/15 = 66.67% · organic: 4/5 = 80%'],
        strength='선택한 제출 SQL이 준비된 검증 데이터의 전체 결과와 일치합니다.',
        improvement='모든 SQL의 동치·독립 역량·학습 효과는 이 검증만으로 확정하지 않습니다.',
        next_action=plain_text(next(c['description'] for c in sql['cards'] if c['title']=='다음 연습')).split('. ', 1)[1])
    assert len(sql['evidence_results'][0]['rows']) == 4
    yield 'sql', 'SQL 결과', '저장된 공개 SQL 제출 · 4행 검증 결과', sql_path, sql, sql_picked

    held = deepcopy(analysis)
    held['evaluation_id'] = 'local-held-fixture'
    held['cards'][0].update(score=75, held=True)
    held['cards'][0]['description'] = '튜토리얼 완료율 분석\n평가 보류: 평가 응답 검증 실패 · 점수 미표시'
    held['cards'] = held['cards'][:3] + [dict(title='평가 신뢰성 확인',
        description='평가 응답 검증 실패. 제출 내용 보존 · 모델 점수 미확정 · 학습자 0점이 아닙니다.')]
    hold_picked = dict(picked, hold_reason='평가 응답 검증 실패 · 학습자 0점이 아닙니다.',
                       next_action='평가 응답 검증 문제를 해결한 뒤 저장된 제출을 다시 평가하세요.')
    yield 'held', '평가 보류', '보류 표시 검증용 파생 테스트 자료 · 실제 평가 아님', analysis_path, held, hold_picked

    errors = deepcopy(analysis)
    errors['evaluation_id'] = 'local-error-fixture'
    errors['cards'].append(dict(title='실행 근거 검산',
        description='확인된 계산 오류 1건 · 전체 완료율 80% 주장 → 저장 근거 30/40 = 75% · 수정 필요'))
    error_picked = dict(picked, warning='확인된 계산 오류 1건 · 전체 완료율 80% → 75% · 수정 필요',
                        next_action='전체 완료율을 30/40 = 75%로 수정하고 결론에 미친 영향을 확인하세요.')
    yield 'errors', '계산 오류', '계산 오류 표시 검증용 파생 테스트 자료 · 실제 평가 아님', analysis_path, errors, error_picked


def card_html(embed):
    text = escape(plain_text(embed.description))
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
    accent = f'#{embed.colour.value:06x}'
    return f'<section class="card" style="border-color:{accent}"><p class="author">참가자</p><h2>{escape(plain_text(embed.title))}</h2><div class="body">{text}</div></section>'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    checks, panels = [], []
    for key, label, provenance, source_path, submission, picked in samples():
        before = deepcopy(submission)
        embed = preview_embed(submission, picked)
        current = ResultForumPublisher(None).post_embeds(submission, SimpleNamespace(display_name='참가자'))
        pdf = render_submission_pdf(submission)
        reader = PdfReader(BytesIO(pdf))
        pdf_text = '\n'.join(page.extract_text() for page in reader.pages)
        # Every full public card, including omitted sections, must reach PDF.
        normalized_pdf = re.sub(r'\s+', '', pdf_text)
        for card in submission['cards']:
            assert re.sub(r'\s+', '', plain_text(card['title'])) in normalized_pdf
        assert submission == before
        if key == 'held':
            assert '/100' not in embed.description and '학습자 0점이 아닙니다' in embed.description
            assert '잘한 점:' not in embed.description
        if key == 'errors':
            assert '계산 오류 1건' in embed.description and '80% → 75%' in embed.description
        if key == 'sql':
            assert '5/5개 충족' in embed.description and '/100' not in embed.description
            assert 'WITH answer AS' in pdf_text
        if key in {'analysis', 'errors', 'held'}:
            for field in ('가설', '대안 설명', '데이터 신뢰성', '후속 검증'):
                assert field in pdf_text
            assert '업무 담당자 후속 질문에 대한 답변' in pdf_text
        (OUT / f'{key}.pdf').write_bytes(pdf)
        (OUT / f'{key}-full-public.json').write_text(json.dumps(submission, ensure_ascii=False, indent=2), encoding='utf-8')
        (OUT / f'{key}-embed.json').write_text(json.dumps(embed.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
        old_count = sum(len(e.description or '') for e in current)
        new_count = len(embed.description)
        row = dict(case=key, source=source_path, original_body_chars=old_count,
                   summary_body_chars=new_count, reduction_percent=round((1-new_count/old_count)*100,1),
                   embed_bytes=embed_bytes([embed]), pdf_pages=len(reader.pages),
                   full_cards_preserved=True, input_unchanged=True)
        checks.append(row)
        old_html = ''.join(card_html(e) for e in current)
        panels.append(f'<article id="{key}" class="panel" {"" if key=="analysis" else "hidden"}>'
            f'<p class="provenance">{escape(provenance)}</p><p class="metrics">본문 {old_count:,}자 → {new_count:,}자 · {row["reduction_percent"]}% 감소</p>'
            f'{card_html(embed)}<a class="download" href="{key}.pdf" target="_blank" rel="noopener">전체 보고서·평가 PDF 다운로드</a>'
            f'<p class="note">전체 {len(submission["cards"])}개 항목은 {len(reader.pages)}쪽 PDF에서 확인할 수 있습니다.</p>'
            f'<details><summary>기존 본문과 비교</summary>{old_html}</details></article>')

    # Long Korean input and Markdown mention escaping stress the compact budget.
    base = next(samples())
    stress_picked = dict(base[-1], title='긴 한글 @everyone **표시** ' * 80,
        conclusion='가나다 **긴 문장** @everyone ' * 100,
        evidence=['한글 근거 ' * 200] * 50, strength='강점 ' * 100,
        improvement='개선 ' * 100, next_action='다음 행동 ' * 200,
        warning='확인된 계산 오류 1건 ' * 20)
    stress = preview_embed(base[-2], stress_picked)
    assert '@everyone' not in stress.title + stress.description
    assert stress.description.count('• ') == 3
    assert '…' in stress.description
    checks.append(dict(case='long_korean_mentions', summary_body_chars=len(stress.description),
                       embed_bytes=embed_bytes([stress]), passed=True))

    html = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Discord 결과 템플릿 테스트</title><style>
*{box-sizing:border-box}body{background:#202126;color:#eee;font:16px/1.55 "Malgun Gothic",sans-serif;margin:24px auto;max-width:820px;padding:0 18px}h1{font-size:25px;margin:0 0 8px}header p,.note,.provenance{color:#b7bac4;font-size:13px}nav{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}button{background:#34363e;color:white;border:1px solid #555861;border-radius:7px;padding:9px 16px;font:inherit;cursor:pointer}button[aria-pressed=true]{background:#5865f2;border-color:#5865f2}.metrics{font-size:14px;color:#b6c8ff;margin:12px 0}.card{background:#2b2d33;border-left:4px solid;border-radius:5px;padding:14px 20px;overflow-wrap:anywhere}h2{font-size:19px;margin:0 0 12px}.author{font-size:12px;color:#abb1bf;margin:0 0 4px}.body{white-space:pre-wrap}.body strong{font-size:14px;color:#acbdd8}.download{display:inline-block;margin-top:12px;border-radius:5px;background:#5865f2;padding:10px 16px;color:white;text-decoration:none;font-size:14px}details{border-top:1px solid #42454d;margin-top:22px;padding-top:14px}summary{cursor:pointer;font-size:14px}details .card{margin-top:16px}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #c0d2ff;outline-offset:3px}@media(max-width:480px){body{padding:0 12px;font-size:14px}.card{padding:12px 14px}h1{font-size:21px}h2{font-size:17px}}
</style><header><h1>Discord 결과 템플릿 테스트</h1><p>로컬 확인본 · 운영 변경 없음 · 요약 문장은 원문에서 선택한 발췌입니다.</p></header><nav aria-label="테스트 사례">'''
    for key, label in [('analysis','분석 결과'),('sql','SQL 결과'),('held','평가 보류'),('errors','계산 오류')]:
        html += f'<button data-case="{key}" aria-pressed="{str(key=="analysis").lower()}">{label}</button>'
    html += '</nav>' + ''.join(panels) + '''<script>
document.querySelectorAll('button[data-case]').forEach(button=>button.addEventListener('click',()=>{
document.querySelectorAll('.panel').forEach(panel=>panel.hidden=panel.id!==button.dataset.case);
document.querySelectorAll('button[data-case]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
}));</script></html>'''
    (OUT/'index.html').write_text(html, encoding='utf-8')
    (OUT/'checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
