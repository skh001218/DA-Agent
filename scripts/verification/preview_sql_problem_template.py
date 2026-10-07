"""Capture actual local SQL transport messages and dictionary PNGs for review."""
import asyncio
from html import escape
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests/automated')]
from da_agent.discord_tables import render_table_png
from da_agent.discord_transport import DiscordTransport
from test_discord_sql_template import sample_document, resume_response

OUT = ROOT / 'tests/artifacts/sql-problem-template-2026-10-07'


async def capture(case, level, generated, fail_images=False):
    doc = sample_document(level, generated)
    response = resume_response(doc)
    events = []
    class Gateway:
        async def send(self, channel, text):
            events.append(dict(kind='text', text=text))
            return NS(id=len(events))
        async def send_table(self, channel, table):
            if fail_images:
                raise PermissionError('local attachment failure fixture')
            for index, page in enumerate(render_table_png(table)):
                filename = f'{case}-{len(events)}-{index}.png'
                (OUT / filename).write_bytes(page)
                events.append(dict(kind='image', title=table['title'], src=filename))
            return [str(len(events))]
    transport = DiscordTransport(NS(bind_sql_prompt=lambda *args: None), Gateway(), ['guild'])
    await transport._emit(None, response['messages'], doc, response['tables'])
    fragments = []
    for event in events:
        if event['kind'] == 'text':
            # Display authored plain text as Discord displays escaped markup.
            value = re.sub(r'\\([\\`*_{}\[\]()<>|~#])', r'\1', event['text'])
            fragments.append('<pre class="message">'+escape(value)+'</pre>')
        else:
            fragments.append(f'<button class="image" aria-label="{escape(event["title"])} 확대">'
                f'<img src="{event["src"]}" alt="{escape(event["title"])}"></button>')
    return events, f'<article id="{case}"><h2>{case}</h2>'+''.join(fragments)+'</article>'


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cases, fragments = {}, []
    for generated in (True, False):
        for level in ('beginner', 'intermediate', 'advanced'):
            name = ('generated' if generated else 'tutorial')+'-'+level
            events, html = await capture(name, level, generated)
            cases[name] = events
            fragments.append(html)
    events, html = await capture('attachment-failure', 'advanced', True, True)
    cases['attachment-failure'] = events
    fragments.append(html)
    (OUT / 'transport.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding='utf-8')
    html = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SQL 문제 템플릿 로컬 확인</title><style>
body{margin:0;background:#313338;color:#f2f3f5;font:16px/1.65 "Malgun Gothic",sans-serif}
main{max-width:800px;margin:24px auto;padding:0 20px}h1{font-size:24px}h2{font-size:18px;color:#b5bac1}
article{border-top:1px solid #52545c;margin:28px 0;padding-top:12px}
pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}.message{background:#2b2d31;padding:18px;border-radius:8px}
.image{border:0;background:transparent;padding:0;display:block;width:100%;cursor:zoom-in;margin:12px 0}
.image img{max-width:100%;display:block;border-radius:8px}a{color:#a6b8ff;margin-right:12px}
dialog{max-width:95vw;max-height:95vh;padding:8px;border:0;background:#202126;color:white}
dialog img{max-width:90vw;max-height:80vh;object-fit:contain}dialog::backdrop{background:#000b}
</style><main><h1>SQL 문제 템플릿</h1><p>로컬 고정 예시 · 실제 전송 코드와 이미지 생성기 사용 · 운영 Discord 화면과 구분</p><nav>'''
    html += ''.join(f'<a href="#{name}">{name}</a>' for name in cases)
    html += '</nav>'+''.join(fragments)+'''</main><dialog><button id="close">닫기</button><br><img alt="확대한 데이터 사전"></dialog>
<script>const modal=document.querySelector('dialog');document.querySelectorAll('.image').forEach(button=>button.addEventListener('click',()=>{modal.querySelector('img').src=button.querySelector('img').src;modal.showModal()}));document.querySelector('#close').addEventListener('click',()=>modal.close());</script></html>'''
    (OUT / 'index.html').write_text(html, encoding='utf-8')
    print(OUT / 'index.html')


if __name__ == '__main__':
    asyncio.run(main())
