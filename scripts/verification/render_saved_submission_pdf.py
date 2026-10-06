"""Render only an exported public submission and verify full text preservation."""
import json
import sys
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from da_agent.discord_pdf import render_submission_pdf, plain_text
from da_agent.discord_pdf_evidence import display_reference_text

source, target = map(Path, sys.argv[1:3])
submission = json.loads(source.read_text(encoding='utf-8'))
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(render_submission_pdf(submission))
reader = PdfReader(target)
compact = lambda value: ''.join(value.split())
text = compact(''.join(
    line for page in reader.pages for line in page.extract_text().splitlines()
    if not line.startswith('DA-Result |') and line != 'DA / ANALYSIS REPORT'
    and not line.strip().isdigit()
))
for card in submission['cards']:
    assert compact(plain_text(card['title'])) in text
    displayed = display_reference_text(plain_text(card.get('pdf_description', card['description'])), submission.get('evidence_results', []))
    assert compact(displayed) in text, card['title']
    if card.get('source_url'):
        assert compact(card['source_url']) in text
print(f'{target.resolve()} | {len(reader.pages)} pages | all public cards preserved')
