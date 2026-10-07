# 3쪽 학습형 결과 PDF 템플릿 검증

- 날짜: 2026-10-07
- 브랜치: `codex/concise-result-pdf`
- 사용자 선택: 요약·분석 근거·평가 피드백 각 1쪽
- 범위: 재사용 템플릿, 명시적 요약 입력 예제, 로컬 생성기와 실제 제출 기반 시안. Discord 게시·다운로드 기본값 및 운영 배포 변경 없음.

## 결과물과 구성

- `template/discord-result-summary.md`: 구역별 작성 규칙·분량·원문 보존 원칙.
- `template/discord-result-summary.example.json`: 실제 제출 원문과 저장 조회를 편집한 요약 입력.
- `scripts/preview_result_pdf.py`: 입력을 3쪽 PDF로 생성. 새로운 모델 호출이나 SQL 실행 없음.
- `output/pdf/concise-result-preview.pdf`: 최종 시안.
- `tests/fixtures/result-pdf-preview-evidence.json`: 기존 실제 E2E 공개 결과에서 추출한 수치·관측 등급·보류 상태. 개인 식별자·비밀 구성 제외.

| 페이지 | 핵심 구성 |
| --- | --- |
| 1 | 범위, 결론, 채널별 표, 조사 우선순위, 주요 해석 한계 |
| 2 | 경험별 표, 근거 해석, 분모·관측 조건·구성 차이·무결성 점검 |
| 3 | 평가 5항목 표, 다음 개선 행동, 평가 상태, 원문 링크 |

## 내용과 시각 검증

- 원본 상세 9쪽 / 시안 3쪽.
- 추출 텍스트 6,896자 / 시안 1,961자: 약 72% 감소. 이 수치는 PDF 전체의 텍스트 추출 길이이며 내용 전체가 보존된다는 뜻은 아님. 상세 원문은 별도로 보존함.
- 채널별 2행 및 경험별 4행의 대상·완료·미완료·완료율을 실제 저장 조회와 대조하여 일치 확인.
- 관측 4/4를 확정 총점으로 바꾸지 않음. 총점 보류, 자동 산술 검산 미실시, 성장 판단 자료 부족 유지.
- 본문 11pt, 표 10pt, 소제목 13pt, 제목 21pt. 캡션 9pt, 꼬리말 8pt.
- 최종 수정 후 Poppler 110dpi로 3쪽 전체 렌더링·시각 확인. 표·한글·여백·페이지 구분·꼬리말에 잘림이나 겹침 없음.
- pdfplumber로 모든 글자의 페이지 경계 안 위치 확인. pypdf로 한글 폰트 내장·링크 목적지 확인.
- 큰 입력은 분량 오류로 중단하며 조용히 자르거나 글자를 줄이지 않음. 표 열 수 오류 및 HTML 특수문자 입력도 확인.

## 자동 검증

```powershell
python -m pytest tests/automated/test_result_pdf_preview.py tests/automated/test_discord_pdf.py -q
```

- 20 passed (신규 4개 + 기존 PDF 16개).
- `git diff --check` 통과.

## 보존과 한계

- 기존 상세 PDF SHA-256: `51782d3066567be83c0bf0690f2e867583160cb63f6c447dc8fbfa45698562fd` (불변 확인).
- 최종 시안 SHA-256: `1d2a73b670c7b6d4af2395e08abacd593b045447829b206a66e8ca0c32b7662b`.
- 검증 JSON과 최종 페이지 PNG: `tests/artifacts/concise-result-template-2026-10-07/`.
- 요약 문장은 원문을 바탕으로 작성자가 편집한 시안이다. 다른 과제를 자동 요약하는 기능은 포함하지 않는다.
- 기존 `/submit`·PDF 다운로드 흐름은 기존 구현을 사용한다. 새 3쪽 형식의 실제 Discord 연동은 아직 실행하지 않았다.
- Codex PDF 패널 열기는 queued 응답을 받음. 시각 검증은 직접 렌더링한 최종 3쪽 이미지로 완료함.
