"""Check summary evidence fidelity, learning-page separation, and overflow safety."""
import importlib.util
import json
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest

pypdf = pytest.importorskip('pypdf')
pytest.importorskip('reportlab')
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('preview_result_pdf', ROOT / 'scripts/preview_result_pdf.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


@pytest.fixture
def example():
    return json.loads((ROOT / 'template/discord-result-summary.example.json').read_text(encoding='utf-8'))


def test_example_matches_actual_saved_evidence_and_held_evaluation(example):
    submission = json.loads((ROOT / 'tests/fixtures/result-pdf-preview-evidence.json')
                      .read_text(encoding='utf-8'))
    overall, detailed = submission['evidence_results']
    percent = lambda value: f'{Decimal(value) * 100:.0f}%'
    assert example['metrics']['rows'] == [
        [r[0], str(r[1]), str(r[2]), percent(r[4]), str(r[3])] for r in overall['rows']]
    assert example['details']['rows'] == [
        [r[0], r[1].removeprefix('경험 '), f'{r[3]} / {r[2]}', percent(r[5]), str(r[4])]
        for r in detailed['rows']]
    criteria = [c for c in submission['cards'] if '모델 관측 등급' in c['title']]
    assert len(criteria) == len(example['feedback']['rows']) == 5
    for card, row in zip(criteria, example['feedback']['rows']):
        assert row[0] == card['title'].split(' · ')[0]
        assert f"모델 관측 등급 {row[1].replace(' ', '')}" in card['title']
    assert any('점수 보류' in c['description'] for c in submission['cards'])
    assert '총점 보류' in example['evaluation_status'][0]


def test_learning_pdf_has_three_separate_pages_and_no_confirmed_total(example):
    before = deepcopy(example)
    pdf = preview.render_preview(example)
    pages = pypdf.PdfReader(BytesIO(pdf)).pages
    assert len(pages) == 3
    texts = [p.extract_text() for p in pages]
    assert '핵심 결론' in texts[0] and '우선 행동' in texts[0]
    assert '게임 경험별 비교' in texts[1] and '제출 보고서의 무결성 점검' in texts[1]
    assert '평가 피드백' in texts[2] and '총점 보류' in texts[2]
    assert '100점' not in ''.join(texts) and '100/100' not in ''.join(texts)
    assert b'/FontFile2' in pdf
    links = [a.get_object() for a in pages[2]['/Annots']]
    assert any(a.get('/A', {}).get('/URI') == example['source_url'] for a in links)
    assert example == before


def test_oversized_summary_is_rejected_without_silent_truncation(example):
    example['conclusion'] = '원문을 숨기거나 글자를 줄이지 않아야 합니다. ' * 700
    with pytest.raises(ValueError, match='3쪽 분량을 초과'):
        preview.render_preview(example)


def test_user_text_is_literal_and_malformed_table_fails(example):
    example['conclusion'] = '<b>원문</b> & 해석'
    pdf = preview.render_preview(example)
    assert '<b>원문</b> & 해석' in pypdf.PdfReader(BytesIO(pdf)).pages[0].extract_text()
    example['details']['rows'][0].pop()
    with pytest.raises(ValueError, match='열 수가 일치'):
        preview.render_preview(example)
