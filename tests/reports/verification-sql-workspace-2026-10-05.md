# SQL 작업 탭과 문법 강조 검증

- 날짜: 2026-10-05 (Asia/Seoul)
- 브랜치: codex/simplify-training-request (기존 UI 작업에서 계속)
- 프로젝트 구조: 표준 폴더와 specs/examples 모두 존재. 구조 변경 없음.
- 사용자 선택: 데이터 사전과 SQL 편집기를 하나의 SQL 작업 탭에 배치.

## 실제 검증

`scripts/verification/verify_sql_workspace_browser.cjs`로 현재 소스와 실제 배포 앱 각각 **12개 검증 통과**. 실제 Edge와 현재 PostgreSQL API를 사용했고 AI 호출은 하지 않았다.

1. 분석 탭에서 사전·SQL 작업대가 제거되고 SQL 탭에서 함께 표시.
2. SQL 탭 전환과 빈 입력 안내.
3. 실제 키보드 입력의 키워드·문자열·숫자·주석 색상 구분과 줄 번호.
4. 입력·교체·되돌리기와 정확한 원문 유지.
5. 탭 전환 시 SQL 유지.
6. 주석·한국어 문자열이 포함된 SQL의 실제 PostgreSQL 실행 성공과 선택 저장 원문 일치.
7. 잘못된 컬럼 SQL의 오류 안내와 입력·기존 결과 보존.
8. 390px 화면과 긴 한국어 문자열 줄바꿈, 페이지 가로 넘침 없음.
9. 새로고침 시 임시 SQL·결과 초기화와 저장 근거 복구.
10. Tab 키로 편집기에서 실행 버튼으로 이동 가능.
11. 브라우저 실행 오류와 CSP 오류 없음(기존 favicon.ico 404는 제외).
12. 편집기 라이브러리를 불러오지 못한 상황에서도 기본 textarea SQL 실행 성공.

검증마다 새로 만든 고정 훈련 한 개에서만 SQL 실행·선택 저장을 수행하고 그 훈련 ID만 삭제했다. 사용자 기존 훈련은 수정하지 않았다. 초기 검증에서 요청 기반 패키지 시작 차단과 DELETE의 JSON 본문 필요를 확인해 스크립트를 수정했으며, 초기 정리 실패로 남은 검증 ID도 삭제했다. 최종 검증의 owned_test_cleanup=true를 확인했다.

기존 UI 소스 브라우저 검사 23개도 통과했고, 현재 앱의 새 훈련·이어 하기·기존 문제 재개·목록 복귀 검사도 통과했다. app.js, sql-editor.js 및 관련 브라우저 스크립트 문법 검사와 git diff --check 통과. 이번 변경은 UI이며 Python 서버 계약을 수정하지 않았다.

## 실행 앱 적용

현재 앱이 다른 작업의 재빌드로 main의 기존 화면으로 돌아간 상태를 발견했다. 이전 배포 파일을 기준으로 한 병합 충돌은 적용 전에 차단했다. 실제 실행 파일과 HEAD를 다시 대조해 화면 파일의 기존 변경을 확인하고 앞서 요청한 메인 화면 정리·이어 하기 탭과 이번 SQL 탭을 함께 반영했다.

현재 컨테이너 static 디렉터리를 백업한 뒤 HTML, app.js, styles.css, training.js와 SQL 편집기 자산을 반영했다. training.js의 기존 코칭 객체 표시 수정은 3방향 병합으로 보존했다. main 작업 폴더와 서버 프로세스·DB 설정은 변경하지 않았다. /api/health 정상.

- 백업: `C:/Users/arusm/AppData/Local/Temp/da-agent-sql-ui-deploy-670ba1c9-384b-4cb1-ae23-a2a258aee7c9/live-static`
- 현재 앱: http://127.0.0.1:8087
- 배포 검증: [결과 JSON](../artifacts/sql-workspace-browser-2026-10-05/live-result.json)
- 화면: [데스크톱](../artifacts/sql-workspace-browser-2026-10-05/live-sql-1280.png), [모바일](../artifacts/sql-workspace-browser-2026-10-05/live-sql-390.png)

컨테이너 재생성 시 유지하려면 이 브랜치의 변경을 배포 소스에 통합해야 한다. 이번 사용자 작업은 현재 실행 앱에 정적 파일을 적용한 범위다.

## 재현

```powershell
$env:PLAYWRIGHT_MODULE='C:/Users/arusm/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
# 실제 배포 앱 검증
node scripts/verification/verify_sql_workspace_browser.cjs
# 현재 소스 UI와 실제 API/DB를 연결해 검증
$env:VERIFY_LOCAL_UI='1'
node scripts/verification/verify_sql_workspace_browser.cjs
```
