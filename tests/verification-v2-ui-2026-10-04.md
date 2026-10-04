# 요청 기반 화면 실제 검증 (2026-10-04 KST)

## 환경과 범위

- 실제 서버: `http://127.0.0.1:8087`, PostgreSQL, 현재 Gemini 연결.
- 검증 도구: Codex In-app Browser의 CUA 접근성 트리·Playwright API. API 보조 검증은 Python urllib/CSV 파서.
- UI 작업: `src/da_agent/static/{index.html,app.js,training.js,quality.html,quality.js,styles.css}`.
- 전용 생성 훈련: `d58d26bc-4398-40c4-9f64-3c918d0c5a27`. 기존 사용자 훈련은 변경·삭제하지 않음. 전용 기록은 리뷰 오류 재현을 위해 남김.
- 화면 동작을 확인한 항목과 API만 확인한 항목을 구분한다. 초기 CUA 검증과 이후 독립 브라우저·실제 모델 재검증을 구분한다. Spec 011 전체 품질 승인과 전 환경·유형 검증을 의미하지 않는다.

## 초기 CUA 검증 결과 (수정·재검증 전)

| 흐름 | 수행과 관측 | 결과 |
| --- | --- | --- |
| 기존 데이터 요청 → 실제 모델 → 준비 | 초급 설계, D1~D7 미재접속 설계를 자연어로 입력. 기존 데이터 선택. 요청 접수·설계·검증·ready, 약 1초 서버 경과 표시. 원 버전 v2 분석 화면 자동 이동 | 확인 |
| 지원 상태·추천 | 4가지 생성 능력 draft, 생성 데이터 옵션 비활성·사유, 기존 사용 가능. 근거 5건의 잠정 추천 표시 | 확인 |
| 유형별 제출 | 설계 과제의 SQL 필수 아님 안내. 문제 정의·접근·발견·한계·다음 행동 작성, SQL 근거 연결 없이 보고서 v1 실제 제출 성공 | 확인 |
| SQL 결과 | `SELECT COUNT(*) AS users FROM users` 실제 성공. 200명, 미리보기/전체 1행, 전체 결과 확보, 8/13/14ms | 확인 |
| SQL 선택 저장 | 서버 재시작 직전 실행은 만료 오류·SQL과 결과 화면 유지. 안정된 서버의 새 실행은 저장 완료 | 확인 |
| 제출본 리뷰 | 실제 AI 호출. 첫 UI 호출 및 수정 후 API 재시도 모두 `review_format` 실패. 화면에 재시도 필요와 고정 조건 확인 실패를 표시하고 제출본 유지 | **미완료: 통합 담당에게 오류 전달** |
| 원 버전 재개 | 새 탭에서 전용 attempt URL 열기. 초급 설계 제목, 저장 SQL 1건·200명 결과, 보고서 v1, 실패 리뷰 2건 복원. API로도 저장 1/보고서 1/리뷰 2 확인 | 확인 |
| 규칙 검토 표본 | 운영자 화면에서 계산 규칙의 초급·중급·고급 표본 실제 생성. 3건 validated, 공개 초급 과제 펼쳐 확인. 승인 버튼 클릭하지 않음 | 확인 |
| 1280px | 분석·보고서 화면 스크린샷 실제 확인. 제출 내용·오류·주요 버튼 표시 | 확인 |
| 390px | 운영자 탭 viewport 390px 설정, DOM 측정 innerWidth=390, document scrollWidth=375. 학습 탭은 당시 1280px로 유지된 것을 확인하고 구분함. 임시 viewport 원복 | **부분 확인** |
| 콘솔 | 실제 요청·SQL·보고서·리뷰 단계에서 오류 로그 목록 없음 | 확인 |
| 운영 지표 장애 | 최초 실제 GET 500 텍스트 오류. 화면에서 응답 해석 실패 안내, 입력 유지. 담당 수정 후 아래 API 검증 수행 | 오류 감지 확인 |
| 8종 지표·내보내기 API | 수정 후 GET/JSON export 200. 날짜 2026-10-04 및 domain access 필터와 metrics_version quality-metrics-v2, 8개 지표 필드 확인. CSV 200 실제 본문 파싱 63행, 정의 버전 열 포함 | **API 확인; UI 다운로드 미확인** |

## 초기 CUA 확인 제한과 당시 남은 검증

훈련 목록 이동의 기존 `window.confirm`에서 CUA의 CDP 마우스 입력이 시간 초과했다. `getJsDialog`는 undefined를 반환했고, 이후 동일 브라우저의 새 탭은 읽기·URL 이동은 가능했으나 버튼 클릭이 적용되지 않았다. 문서의 Escape 키, 다른 새 탭, 탭 종료를 시도했으며 Chrome·Edge는 연결되어 있지 않다. 실제 연결 목록은 IAB와 MCP Apps뿐이다.

이를 줄이기 위해 삭제 확인·미저장 이동·제출본 교체 확인을 기본 취소 초점과 Escape 취소를 제공하는 비동기 HTML dialog로 구현했다. 기존 데이터와 입력을 자동 폐기하지 않는다. 당시 변경 후 키보드·삭제 취소 확인은 제한 때문에 대기였고, 아래 독립 브라우저 재검증에서 삭제 취소를 확인했다.

다음 항목은 새 브라우저 연결 또는 확인창 해제 후 실제 확인 필요:

- 삭제 취소 시 DELETE 없음, 목록·원 입력 유지; 삭제 확정과 연결 집계 갱신.
- 학습 선호·사용자 이견 저장, 기대 revision 충돌 후 입력 유지.
- 8종 지표 현재 필터 조회와 브라우저 JSON/CSV 다운로드 파일 내용.
- 추가 질문 답변·취소 경쟁·재시도·명확한 지원 밖 입력 유지.
- 계산·검토·조사 유형의 요청→대화→제출→리뷰→수정 재개.
- 390px 학습 화면 전체, 키보드 주요 행동·확인창 취소.
- actual review_format 오류 수정 후 실제 모델 성공 재검증. 의미적 평가 품질의 사람 승인은 별도.

## 정적 검사

`node --check`로 app.js/training.js/quality.js 확인, `git diff --check` 확인. 비공개 정답이나 평가 표본을 학습 화면에 추가하지 않았다. 운영자 표본은 서버 공개 sample 응답만 렌더링한다. 네트워크 전체의 비공개 원문 검사는 아직 완료되지 않았다.


## 수정 후 독립 브라우저·실제 모델 재검증

부모 작업의 검증 결과 파일과 검증 스크립트를 읽어 갱신했다. 초기 CUA의 확인창 제한을 현재 미해결 UI 결함으로 간주하지 않는다. 독립 시스템 브라우저를 사용하는 `scripts/verify_v2_browser.cjs`의 실제 서버 검증 결과는 [browser-v2/result.json](browser-v2/result.json)에 저장되어 있다.

| 현재 검증 | 근거와 결과 |
| --- | --- |
| 실제 브라우저·API | actual_browser/actual_api 모두 true |
| 삭제 확인 취소 | delete_cancel=true, DELETE 요청 0건, 훈련 목록 수 유지 |
| 학습 상태 저장 | learning_save=true. 저장 시 state_revision 1 증가, 기존 선호 값 보존 |
| 운영 지표 | metrics_eight=true, 실제 8개 카드와 필터 조회 |
| JSON/CSV 다운로드 | 두 다운로드 모두 true. 실제 파일 내용·필터 확인. [JSON](browser-v2/quality-metrics.json), [CSV](browser-v2/quality-metrics.csv) |
| v2 반복 검증 화면 | quality_v2_controls=true. 실행·종류·대상·진행 확인 UI 조작 |
| 좁은 화면 | width_390=true. [운영자 390px 스크린샷](browser-v2/operator-390.png) |
| 데스크톱 | [1280px 학습 화면](browser-v2/home-1280.png) |
| 콘솔 | console_clean=true, console_errors=[] |

[실제 모델·DB 재검증](verification-v2-live-2026-10-04.json)에서는 Gemini `gemini-3.5-flash-lite`와 실제 DB로 request-v2 출제 ready, SQL 성공, 리뷰 completed, 저장 재개 및 전용 검증 기록 삭제를 확인했다. 초기 `review_format` 실패만을 현재 리뷰의 결과로 인용하지 않는다. 이 재검증의 attempt_id는 `89e2e66a-9f48-4e5c-9bcb-6e01829cd0a5`이며 전용 기록만 삭제했다. 초기 CUA의 훈련과 별도 검증이다.

## 실제 반복 평가 결과와 남은 승인

[실제 반복 평가](verification-v2-quality-live-2026-10-04.json)는 고정 계획의 평가 표본 6개를 3회씩, 18회 호출했다. 호출 기록 18개가 보존되었고 최종 status=completed이나 verdict=fail, semantic_approval=false이다.

- 불확실성 표본의 점수 범위 25점이 허용 편차 10점을 넘었다.
- 비공개 요청 표본 2·3회차가 api_rate_limited로 실패했다.
- 나머지 자동 판정 통과는 사람의 의미적 품질 승인을 대체하지 않는다. human_quality=pending이다.
- 새 생성 규칙은 draft다. 초급·중급·고급의 실제 검증 표본을 생성한 뒤 운영자가 내용을 읽고 승인해야 생성 데이터 사용이 활성화된다. 이번 작업은 승인 버튼을 대신 누르지 않았다.

추가 질문·취소 경쟁·모든 과제 유형의 전체 흐름, 코칭 반복의 실제 품질, 사용자 이견·판정 충돌의 모든 화면 사례, 비공개 응답 전체 검사는 개별 근거를 추가해야 한다. 다른 PC·ARM 실행과 실제 5명 파일럿은 미수행이다. 위 기능 동작 확인을 학습 효과나 의미적 평가 품질 승인으로 확대하지 않는다.


2026-10-04 최종 보완: 호출 간격 5초로 18회 모두 응답했고, 없는 판단의 평가 기준을 명확히 한 결과 근거 부족·불확실성 표본은 편차0이었다. 핵심 오류 표본의 편차12.5가 기준10을 초과해 [최종 반복 결과](verification-v2-quality-live-2026-10-04-final.json)의 verdict=fail을 유지한다. 신규 규칙 네 개의 세 수준 검토 표본12개는 준비됐으나 실제 사람 승인은 대기다. 전체 DB 회귀는196개 통과했다.


[확장 실제 화면 검증](verification-v2-extended-browser-2026-10-04.md)에서 키보드 삭제 취소·추천 수준 변경·추가 질문·세 유형 실제 출제/SQL 저장/제출을 확인했다. 검토/조사 리뷰의 보호 실패와 전 유형 재개 미확인은 해당 문서대로 남긴다. 390px 분석 넘침은 min-width:0으로 수정했다. [마지막 실제 화면 결과](verification-v2-final-browser-2026-10-04.json)에서 지원 밖 요청의 명시 범위/대안·원 입력 유지·planning_calls=0과 분석 화면390px 가로 넘침 없음(390/390)을 확인했다. 모델 추가 호출은0회였다.
