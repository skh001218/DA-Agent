"""Render the actual starter payload and full PDFs for local display QA.

This is a fixture preview, not evidence of publication to live Discord.
"""
from html import escape
from pathlib import Path
import re
from types import SimpleNamespace

from da_agent.discord_forum import ResultForumPublisher
from da_agent.discord_pdf import pdf_filename, render_submission_pdf, plain_text


def main():
    output = Path('tests/artifacts/discord-single-post-2026-10-07')
    output.mkdir(parents=True, exist_ok=True)
    samples = []
    for kind in ('short', 'long'):
        cards = [dict(title='제출 결과 요약', description='튜토리얼 완료율 분석\n평가 점수: 75/100 · 사람 검토 대기.', score=75)]
        report = '채널별 완료율을 비교했습니다. 관측만으로 원인을 확정하지 않습니다. '
        cards.append(dict(title='제출 보고서', description=report * (1 if kind == 'short' else 200), pdf_kind='report'))
        cards.extend([
            dict(title='후속 답변', description='동일 가입 기간과 관측 기간으로 비교했습니다.', pdf_kind='followup'),
            dict(title='실행 근거 해석 · 등급 3/4', description='평가 근거: 분자와 분모를 구분했습니다.\n다음 개선 행동: 채널별 표본 수를 보완하세요.'),
            dict(title='한계 판단 · 등급 3/4', description='평가 근거: 관측 자료의 한계를 밝혔습니다.\n다음 개선 행동: 다른 기간에서도 검증하세요.'),
            dict(title='다음 연습 제안', description='집단별 표본 수와 후속 검증 계획을 보완하세요.'),
            dict(title='학습 관측과 판단 한계', description='성장 판단 자료 부족 · 사람 검토가 필요합니다.'),
        ])
        submission = dict(session_id='preview', evaluation_id=kind, post_name='첫 포스트 통합 미리보기', cards=cards)
        embeds = ResultForumPublisher(None).post_embeds(submission, SimpleNamespace(display_name='참가자'))
        assert len(embeds) <= 10 and sum(len(e) for e in embeds) <= 6000
        filename = pdf_filename(submission)
        (output / filename).write_bytes(render_submission_pdf(submission))
        sections = ''.join('<section><h3>' + escape(e.title) + '</h3><pre>' +
            re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', escape(plain_text(e.description))) + '</pre></section>' for e in embeds)
        samples.append(f'<article id="{kind}"><h2>{"짧은 결과: 전체 본문" if kind == "short" else "긴 결과: 항목별 미리보기 + 전체 PDF"}</h2>'
            f'<p>첫 포스트 메시지 1개 · 평가 답글 0개 · 임베드 {len(embeds)}개 · {sum(len(e) for e in embeds):,}자</p>'
            f'{sections}<a class="download" href="{filename}">PDF 다운로드</a></article>')
    html = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>첫 포스트 통합 표시 검증</title><style>'
    html += 'body{background:#202126;color:#eee;font:16px/1.65 "Malgun Gothic",sans-serif;margin:36px auto;max-width:850px;padding:0 20px}'
    html += 'section{background:#2c2d33;border-left:4px solid #315d91;border-radius:8px;padding:10px 22px;margin:16px 0}pre{white-space:pre-wrap;font:inherit;overflow-wrap:anywhere}h3{margin:6px 0}a{color:#cdd9ff}.download{display:inline-block;background:#5865f2;color:white;padding:8px 18px;border-radius:6px;margin-bottom:28px}</style>'
    html += '<h1>첫 포스트 통합 표시 검증</h1><p>고정 예시로 만든 로컬 미리보기입니다. 실제 Discord 게시 화면과 구분합니다.</p><nav><a href="#short">짧은 결과</a> · <a href="#long">긴 결과</a></nav>'
    html += ''.join(samples) + '</html>'
    (output / 'index.html').write_text(html, encoding='utf-8')
    print((output / 'index.html').resolve())


if __name__ == '__main__':
    main()
