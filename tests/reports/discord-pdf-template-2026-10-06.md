# 표준 보고서형 템플릿 실제 제출 확인본

- 날짜: 2026-10-06 (Asia/Seoul)
- 요청: 추천안1번을 템플릿으로 정하고 실제 데이터로 생성한 PDF 확인.
- 출처: 실행 DB의 과제 `c60e3d9f-9179-490d-8956-5a3eac24bfb3`, 평가 `82f33262-9c48-4c5f-b707-845638bdac24`.
- 합성 훈련 데이터의 실제 조회·제출·모델 평가 기록이다. 실제 고객 데이터로 해석하지 않는다.
- DB 원본 전체를 내보내지 않고 실행 컨테이너의 `build_submission`을 통해 공개 카드10개만 가져왔다. 모델 호출이나 점수 수정은 없다.
- PDF: `output/pdf/standard-report-2026-10-06/analysis-standard-full.pdf`, 4쪽.
- 원본 공개 제출: `tests/artifacts/discord-pdf-template-2026-10-06/public-submission.json`.

## 템플릿

A4, 여백48pt, 제목22pt, 섹션13pt, 본문11pt/행간18pt, 일반/굵은 한글 폰트 내장, 연한 파란색 제출 요약, 번호가 있는 섹션, 평가 근거/개선 행동/근거 표지, 페이지 번호. 기존 원문 표지 앞에 문단 간격만 추가한다. 짧은 후속·평가 섹션은 같은 페이지에 모아 근거의 마지막 줄이 다음 쪽에 따로 남지 않게 했다.

## 확인

- 생성 스크립트에서 줄바꿈·반복 머리말·꼬리말을 제외하고 원본 카드10개의 모든 제목·본문·출처URL과 PDF 추출 내용을 비교했다. 전부 보존됨.
- 4쪽 전체를 Poppler100dpi PNG로 렌더링하고 실제 이미지 확인. 한글, 문단 간격, 페이지 번호, 평가 구분과 페이지 전환 확인.
- Discord 관련 자동 테스트136 passed,19 skipped(기존 환경 의존 테스트). 마지막 페이지 배치 조정 후 PDF 관련13개 테스트 재실행 통과.
- 긴 보고서700회 반복 보존 테스트는 확대된 본문의 줄바꿈과 페이지 머리말/꼬리말을 제외해 실제 본문 보존 여부를 확인하도록 개선.
- `git diff --check` 확인.

현재 변경은 로컬 PDF 생성 코드와 확인본에 반영했다. 실행 중인 봇 이미지를 재배포하거나 기존 포럼 첨부를 교체하지 않았다. 따라서 이전 게시글의 버튼은 기존 파일을 제공한다.

## 재현

```powershell
python scripts/verification/render_saved_submission_pdf.py tests/artifacts/discord-pdf-template-2026-10-06/public-submission.json output/pdf/standard-report-2026-10-06/analysis-standard-full.pdf
pdftoppm -r 100 -png output/pdf/standard-report-2026-10-06/analysis-standard-full.pdf output/pdf/standard-report-2026-10-06/page
```
