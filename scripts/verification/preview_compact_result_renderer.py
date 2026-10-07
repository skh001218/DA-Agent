"""Verify the production renderer from stored public fixture exports.

No new evaluation, model call, Discord post or PDF authoring is performed.
The PDFs are unchanged copies from the earlier verified layout experiment.
"""
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from da_agent.discord_result_summary import analysis_summary, sql_summary
from da_agent.discord_forum import ResultForumPublisher, embed_bytes, MAX_EMBED_BYTES
from da_agent.discord_pdf import plain_text

SOURCE = ROOT / 'tests/artifacts/discord-result-summary-2026-10-07'
OUT = ROOT / 'tests/artifacts/spec055-compact-results-2026-10-07'


def summarized(submission, key):
    original = deepcopy(submission)
    cards = submission['cards']
    title = plain_text(cards[0]['description']).split('\n')[0]
    if key == 'sql':
        rows = []
        for card in cards:
            if card['title'].endswith((' · 충족', ' · 보완 필요', ' · 판정 보류')):
                name, status = card['title'].rsplit(' · ', 1)
                rows.append(dict(name=name, status=status, reason=plain_text(card['description']).split('\n')[0]))
        stored = submission['evidence_results'][0]
        summary = sql_summary({'title': title}, {'full_result': dict(stored, result_complete=not stored.get('truncated'))},
            dict(held=cards[0].get('held'), criteria=rows,
                 limitation=plain_text(cards[0]['description']).split('\n')[-1]))
    else:
        report = '\n'.join(plain_text(c['description']) for c in cards if c.get('pdf_kind')=='report')
        criteria = []
        for card in cards:
            grade = re.search(r'등급 (\d)/4', card['title'])
            body = plain_text(card['description'])
            if grade and '평가 근거\n' in body and '다음 개선 행동\n' in body:
                criteria.append(dict(grade=int(grade[1]), reason=body.split('평가 근거\n')[1].split('\n\n')[0],
                    improvement=body.split('다음 개선 행동\n')[1].split('\n\n')[0]))
        result = dict(total=cards[0].get('score'), held=cards[0].get('held'), criteria=criteria)
        if key == 'held':
            result['reason'] = '평가 응답 검증 실패 · 학습자 0점이 아닙니다.'
        if key == 'errors':
            result['arithmetic_verification'] = {'errors': [{'improvement': '전체 완료율을 30/40 = 75%로 수정하세요.'}]}
        summary = analysis_summary({'title': title}, {'content': {'report_text': report}}, result)
    assert submission == original
    return dict(submission, forum_summary=summary)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    panels, checks = [], []
    for key, label in [('analysis','분석 결과'),('sql','SQL 결과'),('held','평가 보류'),('errors','계산 오류')]:
        source = SOURCE / f'{key}-full-public.json'
        submission = summarized(json.loads(source.read_text(encoding='utf-8')), key)
        before = deepcopy(submission)
        embeds = ResultForumPublisher(None).post_embeds(submission, SimpleNamespace(display_name='참가자'))
        assert len(embeds) == 1 and len(embeds[0].description) <= 900
        assert embed_bytes(embeds) <= MAX_EMBED_BYTES and submission == before
        if key == 'held': assert '/100' not in embeds[0].description
        if key == 'errors': assert '계산 오류 1건' in embeds[0].description
        if key == 'sql': assert '5/5개 충족' in embeds[0].description
        # PDFs are byte-for-byte copies of the original full-card fixtures.
        shutil.copyfile(SOURCE / f'{key}.pdf', OUT / f'{key}.pdf')
        (OUT / f'{key}-embed.json').write_text(json.dumps(embeds[0].to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
        description = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', escape(plain_text(embeds[0].description)))
        checks.append(dict(case=key,chars=len(embeds[0].description),bytes=embed_bytes(embeds),input_unchanged=True))
        panels.append(f'<article id="{key}" {"" if key=="analysis" else "hidden"}><p class="note">'
            f'{"파생 경계 테스트" if key in {"held","errors"} else "저장된 공개 훈련 결과"} · 운영용 발췌·게시 렌더러</p>'
            f'<section><h2>{escape(plain_text(embeds[0].title))}</h2><div>{description}</div></section>'
            f'<a href="{key}.pdf" download>전체 보고서·평가 PDF 다운로드</a><p class="note">본문 {len(embeds[0].description)}자 · 임베드 1개</p></article>')
    html = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>운영용 결과 요약 검증</title>
<style>body{background:#202126;color:#eee;font:16px/1.55 "Malgun Gothic",sans-serif;margin:24px auto;max-width:780px;padding:0 20px}h1{font-size:25px}h2{font-size:19px;margin:0 0 12px}nav{display:flex;gap:8px;flex-wrap:wrap}button{background:#34363e;color:white;border:1px solid #555861;border-radius:6px;padding:10px 16px;font:inherit;cursor:pointer}button[aria-pressed=true],a{background:#5865f2}section{background:#2b2d33;border-left:4px solid #315d91;border-radius:5px;padding:16px 20px}section div{white-space:pre-wrap;overflow-wrap:anywhere}strong{color:#acbdd8;font-size:14px}.note{color:#b7bac4;font-size:13px}a{display:inline-block;padding:10px 16px;margin-top:12px;color:white;text-decoration:none;border-radius:5px;font-size:14px}</style>
<h1>운영용 결과 요약 검증</h1><p class="note">추가 AI 호출 없이 저장된 공개 내용에서 발췌 · 실제 Discord 게시 전 로컬 확인</p><nav>'''
    for key,label in [('analysis','분석 결과'),('sql','SQL 결과'),('held','평가 보류'),('errors','계산 오류')]:
        html += f'<button data-case="{key}" aria-pressed="{str(key=="analysis").lower()}">{label}</button>'
    html += '</nav>' + ''.join(panels) + '''<script>document.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('article').forEach(p=>p.hidden=p.id!==b.dataset.case);document.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));}));</script></html>'''
    (OUT / 'index.html').write_text(html,encoding='utf-8')
    (OUT / 'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False))


if __name__ == '__main__':
    main()
