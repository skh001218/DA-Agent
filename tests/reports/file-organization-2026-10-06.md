# 파일 구조 정리 검증

- 확인일: 2026-10-06 (Asia/Seoul)
- 목적: 같은 역할의 파일을 모으고 파일 이동 후 실행·검증 경로를 유지한다.
- 파일·폴더 이동: 130건, 포함된 파일 197개. 기존 목적지 충돌 0건, 이동 대상 보존 확인.

## 정리 결과

| 위치 | 내용 |
| --- | --- |
| `tests/automated/` | 자동 테스트 19개 |
| `tests/reports/` | 기존 검증 보고서·수동 절차 18개와 이번 정리 기록 |
| `tests/artifacts/` | 검증 JSON·이미지·다운로드 결과. 기능별 검증 폴더 유지 |
| `scripts/verification/` | 검증·재실행·내보내기·보고서 도구 30개 |
| `scripts/diagnostics/` | 진단·화면 조사 도구 4개 |
| `.local/work/` | PR 초안·결과·임시 작업 자료 7개 |

설치·DB·데이터 준비 스크립트, `.local` 비밀키, 앱 소스, 훈련 패키지는 기존 위치를 유지한다. `verify_release.py`는 설치 과정에서 쓰는 패키지 지문 검사로서 `scripts/` 바로 아래에 유지한다. 이동 내역과 이동 전 참조 목록은 [이동 기록](../artifacts/file-organization-2026-10-06/moves.json)에 있다.

문서 상대 링크·코드의 결과 읽기/쓰기 경로·하위 스크립트의 프로젝트 루트 계산을 갱신했다. pytest 기본 탐색 위치는 `tests/automated`다. 브라우저 SQL 검증은 `VERIFY_OUTPUT_DIR`로 새 결과 폴더를 지정할 수 있어 기존 검증 산출물을 보존한다.

## 확인 결과

| 검증 | 결과 |
| --- | --- |
| 이동된 파일 존재 | 197개 모두 확인 |
| 이동 전 존재하던 문서 링크 | 289개 모두 이동 후 연결 유지 |
| 자동 테스트 로직 | 19개 모두 AST 비교 일치. 두 파일의 안내 경로만 수정 |
| 앱 소스 | 변경 없음 |
| 호스트 pytest | 245 passed, 44 skipped, 1 기존 의존성 경고 |
| 실제 PostgreSQL 회귀 | 289 passed, 2 기존 의존성 경고 |
| Python·JavaScript 문법 | Python 파싱 정상, JavaScript 검증 스크립트 22개 정상 |
| 패키지 지문·Compose 설정 | `verify_release.py`, `docker compose config --quiet` 성공 |
| 실제 브라우저·DB | SQL 작업 12개 확인, 페이지·콘솔 오류 0개, 검증용 기록 정리 성공 |

실제 DB 회귀는 기존 `da-agent-v3-verification` 컨테이너의 호스트 바인드 경로에서 이동한 `scripts/verification/verify_request_training.py`를 실행했다. 임시 기록 스키마를 사용하고 검사 종료 후 제거하여 사용자 기록을 보존했다.

브라우저 확인은 agent-browser CLI가 미설치여서 기존 Playwright 검증 도구로 실행했다. SQL 입력·빈 입력·실행·오류 입력 보존·선택 저장·새로고침·모바일 화면·기본 입력창 대체 동작을 확인했다. 실제 AI 호출은 0회다. [브라우저 결과](../artifacts/file-organization-2026-10-06/browser/live-result.json), [데스크톱 화면](../artifacts/file-organization-2026-10-06/browser/live-sql-1280.png), [모바일 화면](../artifacts/file-organization-2026-10-06/browser/live-sql-390.png)을 보존한다.

## 범위

이번 결과는 파일 구조 변경 후 자동 테스트·기존 로컬 앱과 DB의 정상 동작 확인이다. 다른 PC의 신규 설치나 AI 평가 품질 검증은 실행하지 않았다. 이전 작업에서 삭제한 미사용 자료는 복원하지 않았고, 이번 정리는 추가 자료 삭제 없이 이동과 참조 수정으로 수행했다.
