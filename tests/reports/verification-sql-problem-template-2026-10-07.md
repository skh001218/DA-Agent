# SQL 문제 템플릿 검증 — 2026-10-07

## 결과와 범위

SQL 연습 안내에 고정 템플릿을 적용하고 2번 사용할 데이터 구역 뒤에 기존 데이터 사전 이미지를 배치했다. 실제 전송 코드와 PNG 생성기로 로컬 미리보기를 만들었다. 로컬 구현·검증 완료이며 PR·main 병합·운영 배포·운영 Discord 확인은 수행하지 않았다.

## 구현

- 새 생성형 SQL과 고정 튜토리얼 SQL에 공개 구역 정보를 저장한다. 기존 `objective`·SQL 계약·채점·비공개 정답은 변경하지 않는다.
- `discord_sql_presentation.py`가 제목·난이도·목표·데이터·조건·출력·제출을 같은 순서로 표시한다. 신규 과제 생성 후 첫 안내와 재개 모두 서비스 `resume` 응답 경로를 사용한다.
- 첫 안내 메시지의 2번 구역 뒤에 기존 `dictionary_tables`/`render_table_png` 이미지를 모두 첨부하고 두 번째 안내 메시지의 3~5번 구역, 기존 입력 틀을 이어 보낸다.
- 이미지 실패 시 기존 공개 행 목록 대체 기능을 사용한다. 입력 틀의 네이티브 코드 블록과 답장 대상 연결도 유지한다.
- 이전 저장 과제에 새 구역 정보가 없으면 공개 원문을 계산 조건 구역에서 보존한다. 레거시 자유 형식 원문을 임의로 요약·재해석하지 않으므로 신규 문제보다 해당 구역이 길 수 있다.

## 자동 검사

다음 명령에서 **92 passed / 18 skipped**를 확인했다. 18개는 격리 PostgreSQL DSN이 필요한 기존 DB 검사이며 이번 실행에서는 환경이 없어 생략했다. DB 실행·채점의 실제 동작을 새로 검증했다고 주장하지 않는다. Discord 라이브러리의 기존 audioop 폐기 예정 경고 1개가 있었다.

```powershell
python -m pytest -q tests/automated/test_discord_sql_template.py tests/automated/test_discord_generated_sql.py tests/automated/test_discord_sql_practice.py tests/automated/test_discord_basic_template.py tests/automated/test_discord_tables.py tests/automated/test_discord_transport.py tests/automated/test_discord_generation.py --junitxml=tests/artifacts/sql-problem-template-2026-10-07/regression.xml
```

새 검사 9개는 생성형/고정×3난이도의 공개 조건·출력 열 순서·NULL·고급 설명 보존, 저장 원문·긴 메시지·멘션 처리, 모델 재호출 없는 재개, 실제 PNG 생성, 첨부 실패 시 정보 보존, 이미지 배치 순서와 입력 틀 답장 연결을 확인한다. 관련 기존 분석 템플릿·출제·전송 회귀도 통과했다.

## 로컬 화면 확인

```powershell
python scripts/verification/preview_sql_problem_template.py
# PLAYWRIGHT_MODULE을 로컬 playwright 모듈 경로로 지정
node scripts/verification/verify_sql_problem_template.cjs
```

- 실제 전송 응답과 생성된 PNG로 만든 6개 모드·난이도 레이아웃에서 데이터 이미지가 조건보다 먼저 표시되는 것을 확인했다.
- Edge에서 이미지 클릭·확대 이미지 로드(원본 폭 1,200px)·닫기, 390px 모바일의 가로 넘침 없음, 새로고침, 첨부 실패 예시의 컬럼 표시를 확인했다. 브라우저 오류는 없었다.
- 데스크톱·모바일 스크린샷을 직접 열어 구역 순서와 줄바꿈을 확인했다. 모바일의 이미지는 축소되므로 확대해 읽는 구성을 유지한다.
- 로컬 HTML의 확대 동작은 레이아웃 검토용이다. 실제 Discord 클라이언트에서 이번 변경을 확인한 것으로 간주하지 않는다.

## 근거

- [미리보기](../artifacts/sql-problem-template-2026-10-07/index.html)
- [데스크톱](../artifacts/sql-problem-template-2026-10-07/desktop.png)
- [모바일](../artifacts/sql-problem-template-2026-10-07/mobile.png)
- [이미지 확대](../artifacts/sql-problem-template-2026-10-07/image-expanded.png)
- [브라우저 결과](../artifacts/sql-problem-template-2026-10-07/browser.json)
- [전송 내용](../artifacts/sql-problem-template-2026-10-07/transport.json)
- [자동 검사](../artifacts/sql-problem-template-2026-10-07/regression.xml)
