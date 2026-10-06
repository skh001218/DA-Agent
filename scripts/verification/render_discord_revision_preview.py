"""Local inspection of actual saved submission cards; no Discord publishing."""
import argparse
import html
import json
from pathlib import Path
from da_agent.discord_results import build_submission

parser=argparse.ArgumentParser()
parser.add_argument('input')
parser.add_argument('output')
args=parser.parse_args()
data=json.loads(Path(args.input).read_text(encoding='utf-8'))
doc=data['session']
scripted=data.get('verification_kind')=='real_record_db_scripted_judgment_stored_query_evidence'
heading='검증용 서비스 결과 카드' if scripted else '저장된 실제 제출 결과 카드'
scope=('격리 기록 DB·검증용 모델 판정·저장 조회 근거로 만든 카드입니다. 실제 Gemma 반복 검사는 별도 기록이며 Discord 전송은 하지 않았습니다.'
       if scripted else '서비스 저장 결과의 표시 검증용입니다. Discord 전송 화면은 별도로 확인해야 합니다.')
heading=data.get('preview_heading',heading)
scope=data.get('preview_scope',scope)
buttons=''.join(f'<button onclick="show({index+1})">'+('최초 제출 v1' if index==0 else f'수정 제출 v{index+1}')+'</button>' for index in range(len(doc['evaluations'])))
sections=[]
for index,entry in enumerate(doc['evaluations']):
    submission=build_submission(doc,entry['id'])
    cards=''.join('<article><h2>'+html.escape(card['title'])+'</h2><pre>'+html.escape(card['description'])+'</pre></article>' for card in submission['cards'])
    sections.append(f'<section id="version{index+1}"'+(' hidden' if index else '')+'>'+cards+'</section>')
page='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>실제 제출 결과 카드 검증</title>
<style>body{font-family:Arial,"Malgun Gothic",sans-serif;background:#edf2f7;color:#172b46;margin:0;padding:24px}main{max-width:940px;margin:auto}button{padding:12px 20px;margin:12px 12px 12px 0;font-size:16px;cursor:pointer}article{border-left:5px solid #366bb0;background:white;padding:20px;margin:18px 0;border-radius:8px}h1{font-size:24px}h2{font-size:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:16px/1.6 Arial,"Malgun Gothic",sans-serif}section[hidden]{display:none}</style>
<main><h1>'''+html.escape(heading)+'''</h1><p>'''+html.escape(scope)+'''</p>'''+buttons+''.join(sections)+'''</main><script>function show(n){document.querySelectorAll('section').forEach(s=>s.hidden=s.id!=='version'+n)}</script></html>'''
Path(args.output).write_text(page,encoding='utf-8')
