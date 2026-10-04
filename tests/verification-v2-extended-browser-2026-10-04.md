# v2 확장 실제 브라우저 검증 — 2026-10-04 KST

## 환경·보존·호출 상한

- 실제 서버 `http://127.0.0.1:8087`, 실제 PostgreSQL 및 현재 Gemini 모델. 고정 응답·모의 API·가짜 표본을 사용하지 않았다.
- 시스템 Chrome headless와 Playwright. 실행 소스: [verify_v2_extended_browser.cjs](../scripts/verify_v2_extended_browser.cjs), 결과: [JSON](verification-v2-extended-browser-2026-10-04.json).
- 모델 작업 상한 8회, 실제 요청 작업 5회 + 리뷰 작업 3회 = 8회 시작. 자동 코칭·추가 답변·재시도로 호출을 늘리지 않았다.
- 최초 기존 훈련 ID 목록을 읽고 새로 만들어진 세 훈련만 변경·정리했다. 세 전용 훈련 삭제는 모두 HTTP 200. 기존 사용자 기록과 생성 규칙 승인은 변경하지 않았다.
- 실행 중 부모 통합 서버가 한 번 재시작되었다. 최종 결과에서 재시작 중단 오류는 관측되지 않았다.

## 결과

| 항목 | 실제 결과 |
| --- | --- |
| 키보드 삭제 취소 | 삭제 버튼 초점 → Enter → 취소 버튼 기본 초점 확인 → Tab → Escape. dialog 닫힘, 기존 훈련 목록 7개 유지, DELETE 요청 0건 |
| 홈 390px | innerWidth=390, scrollWidth=390. 가로 넘침 없음 |
| 추천 수준 변경 | 추천 변경으로 수준·유형을 불러온 뒤 advanced 선택 확인. AI 호출 없음 |
| 지원 밖 요청 입력 유지 | 매출·결제·전투·경제 요청이 실패했고 원문 유지. error_code=plan_invalid로 반환됐으므로 **지원 밖 이유·대안 안내가 올바르다는 검증은 미완료** |
| 명시 선택 충돌·추가 질문 | 문장 고급 설계 / 선택 초급 계산의 충돌로 needs_clarification. 난이도·유형 확인 질문 표시, 원문 유지. 답변 전송은 호출 상한 때문에 수행하지 않음 |
| 계산 과제 | beginner/calculation 명시 선택대로 ready. 실제 SQL 성공·선택 저장·보고서 v1 제출·리뷰 completed |
| 검토 과제 | beginner/review 명시 선택대로 ready. 실제 SQL 성공·선택 저장·보고서 v1 제출. 리뷰는 answer_exposure 실패로 보존 |
| 현상 조사 과제 | beginner/investigation 명시 선택대로 ready. 실제 SQL 성공·선택 저장·보고서 v1 제출. 리뷰는 unverified_calculation 실패로 보존 |
| 분석 화면 390px | 세 유형 모두 innerWidth=390, scrollWidth=452. **가로 넘침 발견** |
| 콘솔 | pageerror 수집 목록 없음 |

제출에 사용한 실제 SQL은 `SELECT count(*) AS users FROM users`다. 보고서는 확인한 전체 유저 수와 아직 미계산인 미재접속 지표를 구분하고, 코호트·관측 조건을 추가 확인할 행동을 적었다. 기능 동작을 시험한 제출이며 의미적 품질 승인용 정답 제출로 취급하지 않는다.

## 결함·스크립트 제한

분석 화면의 가로 넘침은 별도 읽기 전용 진단에서도 확인했다. 기존 검증 전용 설계 훈련을 열었을 때 aside의 left=20, right=452, width=433이었다. JSON의 layout_diagnostics에 DOM 폭을 보존했다. 좁은 화면 grid의 자동 최소 크기와 표의 고유 폭이 원인으로 의심되며 부모 작업에 전달했다. 이 검증 작업은 UI 코드 변경 권한이 없어 코드를 바꾸지 않았다. 부모 작업에서 min-width:0을 적용한 뒤 기존 전용 설계 훈련을 **읽기 전용으로 재검증**했다. innerWidth=390, scrollWidth=390, asideWidth=351로 통과했다. JSON의 checks.analysis_390_after_css_fix에 보존했다. 추가 모델 호출은 0회이며, 최초 세 유형의 넘침 관측도 덮어쓰지 않았다.

재개 단계에서는 검증 스크립트가 제출·리뷰 후 history 탭에 머문 상태로 숨겨진 SQL 입력을 비우려고 해 30초 timeout이 발생했다. 이는 **테스트 스크립트 오류**이며 제품 재개 실패로 판단하지 않는다. 소스는 분석 탭으로 돌아간 뒤 SQL을 비우도록 수정했지만, 모델 호출 8회 상한과 전용 기록 정리 때문에 전체 모델 흐름을 다시 실행하지 않았다. 따라서 세 신규 과제의 재개는 이 기록에서 미확인이다. 기존 [v2 UI 검증](verification-v2-ui-2026-10-04.md)의 별도 성공 재개 근거를 이 세 유형의 성공으로 대신 적용하지 않는다.

리뷰 실패는 화면과 서버가 보존한 결과다. 비공개 내용 차단·미검증 계산 보호가 작동했을 수 있으나, 실패 코드만으로 평가 의미의 타당성을 확정하지 않는다. 사람의 품질 판정은 별도다. 추가 AI 호출 없이 실패를 그대로 전달했다.

## 재실행

`PLAYWRIGHT_MODULE`에 Playwright 설치 경로, `BROWSER_EXECUTABLE`에 시스템 Chrome/Edge 경로를 설정하고 `node scripts/verify_v2_extended_browser.cjs`를 실행한다. 필요하면 VERIFY_BASE_URL로 서버 주소를 바꾼다. 실행은 실제 최대 8회의 모델 작업을 시작하므로 운영자의 호출량·비용을 확인해야 한다. 기존 기록은 최초 ID 목록으로 보호하고 생성 승인 버튼은 누르지 않는다.

정적 확인: 수정된 스크립트의 `node --check`, `git diff --check` 통과. 다른 PC·ARM·실제 5명 파일럿과 모든 오류·추가 답변·취소 경쟁·의미적 리뷰 품질은 이 확장 검증으로 완료하지 않았다.
