# 요청별 공통 생성·검증·복구 확인 — 2026-10-05

## 사용자 목적

보스 클리어 분석에서 고급 요청은 실패하고 초급은 성공했다. 원인은 고급 설계의 FK 오류 수정 이후 적합성 탈락(goal_mismatch)이었고, 재시도에서 호출 상한(planning_limit)이 기존 표시 이유를 덮어썼다. 사용자는 보스 전용 대신 다른 요청에도 적용되는 공통 수정을 요청했다. 특정 시간 정의를 전 제품의 기본값으로 결정하지 않았다.

## 구현한 공통 수정

- `derived_from`/`group_by`, `group_key`/`aggregate`: 요약은 원본에서 조건별 count/distinct/sum/avg/min/max로 계산한다. Python 값과 실제 PostgreSQL 원본의 GROUP BY/FILTER 값을 대조한다. 미관측 집계는 NULL이며 횟수는 0이다.
- `timestamp_sequence`: 개체와 분석 대상별로 이전 종료+대기 시간을 다음 시작으로 사용한다. `timestamp_offset`은 시작+소요 시간을 종료로 만든다. `timestamp_bucket`은 UTC/KST 날짜 또는 시간 버킷을 만든다.
- 선언된 일별 요약에는 원본 날짜 버킷/날짜 범주 집계키를 요구한다. 숙련도 비교에는 공개 근거 컬럼을 요구한다. 설명과 요청에서 원본·요약을 함께 요구하면 실제 파생 요약이 있어야 한다. 로그와 연결된 프로필의 도전 횟수/평균/성공률을 독립 난수로 만들지 못하도록 검사한다.
- 고정 seed로 DB 접근 전에 표본·집계·시간·행 수의 실현 가능성을 검사한다. 형식/고정 데이터 수정 최대2회, 적합성 수정 최대1회, 합계 최대6회이며 최종 적합성 검증 호출을 위한 예산을 남긴다. 적합성 수정안의 형식 오류도 남은 예산에서 수정 후 재검증한다.
- 비공개 `schema_reviews`/`alignment_reviews`에 판정 이유를 보존하고 수정 입력으로 사용한다. 공개 `failure_history`에는 안전한 실패 코드/설명/시각을 보존한다. 소진된 재시도는 기존 상태·원인·수정 번호를 변경하지 않는다. 화면에서 최초 실패와 새 요청 안내를 표시한다.
- Gemini adaptive 설계/검증 호출은 JSON MIME 모드와 출력 상한 8192를 사용한다. 일반 코칭/리뷰의 기존 출력 설정은 유지한다. 참고한 공식 문서: [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output?hl=en). JSON 모드는 의미적 적합성 보장이 아니며 서버 검증을 유지한다.
- 기본값만 추가된 기존 recipe의 해시를 유지하여 이미 고정한 패키지와의 충돌을 피한다. 기존 사용자 기록과 데이터베이스 볼륨을 초기화하지 않았다.

## 실제 DB 회귀

최종 이미지에서 `docker compose exec -T app python scripts/verification/verify_request_training.py /tmp/tests`: **242 passed**, 107.37초, 기존 라이브러리 deprecation 경고2건. 고유 disposable recorder schema를 생성·제거하며 사용자 기록은 보존한다.

추가 회귀는 여러 주제의 시간/요약 일치, 잘못된 원본·집계키·타입·시각·프로필 연결 거부, KST 날짜 버킷, 원본 SQL 집계 대조, 적합성 회복·최종 실패 미공개, 수정안 형식 오류 회복, 소진된 레거시 재시도의 최초 실패 보존, 고정 표본 부족 수정, provider JSON 설정 분리를 확인한다. 문법 검사와 git diff --check도 통과했다.

## 실제 모델과 Chrome

`scripts/verification/verify_adaptive_repair_browser.cjs`와 `../artifacts/adaptive-repair-browser-2026-10-05/result.json`이 최종 증거다. 모델 호출을 대체하지 않았다. 최종 실행에서는 새 보스 과제를 생성했고, 이미 실제 모델로 생성한 최종 재화·구매 과제는 같은 요청을 재개하여 최신 서버에서 화면/SQL/저장/재개를 확인했다. 세 과제 모두 advanced, ready, 페이지 오류0, SQL 성공·저장·초안 재개·390px 가로 넘침 없음. 보스 화면 스크린샷은 직접 시각 확인했다.

최종 서버 재빌드 뒤 `restart-result.json`에서 모델 추가 호출 없이 세 과제와 저장 SQL의 화면 재개를 확인했다. `failure-state.png`는 실제 기존 실패 요청의 입력 유지·재시도 버튼 숨김·새 요청 안내를 확인한 화면이다. 신규 공개 출제 이유는 서버에서 학습 목적의 문장으로 고정해 모델의 스키마 수정 설명이 학습 화면에 노출되는 것을 방지했다.

| 요청 | 요청 ID / 과제 ID | 실제 자료 | 결과 |
| --- | --- | --- | --- |
| 원 사용자 문장과 동일한 고급 보스 클리어 분석 | b8e0d9dc-a276-4dd5-b233-e59a051ee932 / c7b331b8-435b-4b79-9a12-e01cdaca3ba0 | account_profile, boss_try_log, daily_boss_summary | 설계 오류 수정 후 총3회 호출, ready |
| 원본 로그·일별 요약을 요구한 고급 재화 분석 | dd8cf717-26ce-4e34-99f9-90aa21da6a26 / 1e48776c-5633-403e-bcd1-429874c57d09 | currency_log, currency_daily_summary | 빠진 요약 수정 후 총3회 호출, ready |
| 최초 구매 시도부터 최초 성공까지 고급 분석 | 00c3510d-9880-4139-b2de-8b06b638edce / 1e9f541f-f8d4-4e10-9c7c-cbeb2722d2f6 | account_profile, purchase_attempt_log | FK 수정 후 총3회 호출, ready |

## 중간 실패도 보존

- `initial-failure.json`: 프로필 FK 그룹 오류의 진단 구체성이 부족해 설계 수정 실패. 구체적인 표·그룹·컬럼·기대 참조를 오류에 추가했다.
- `pre-content-guard-result.json`: UI/API 성공이지만 요약 미제공·날짜 없는 일별 요약·공개 숙련도 자료 부족을 발견. 최종 성공 증거에서 제외했다.
- `json-format-failure.json`: 3회 모두 잘못된 JSON. provider JSON 모드를 적용했다.
- `pre-profile-summary-guard-result.json`: 프로필의 도전 총계를 로그와 독립 생성한 중간 보스 표본. 최종 보스 증거에서 제외했다.
- `alignment-correction-schema-failure.json`: 적합성 탈락의 수정안이 형식 검증에 실패했다. 사유 기록을 공통 parse 경로에 연결하고 남은 예산에서 형식 수정·최종 재검증하도록 보완했다.

실패한 요청을 성공으로 덮어쓰지 않는다. 위 중간 과제는 최종 품질 증거가 아니며, 자동 판정에 합격했던 중간 사례도 이후 발견한 결함을 기록했다.

`obsolete-trials.json`의 중간 시험 과제4개는 이 작업이 생성한 요청/과제/패키지 ID가 일치하고 추가 사용자 대화·보고서가 없는지 확인한 뒤 목록에서 제거하고 학습자 DB 접근을 회수했다. 데이터 파일과 중간 증거는 보존했다. 최종 과제3개와 기존 사용자 기록은 보존했다.

## 남은 한계

자동 생성에는 여전히 모델·통신·지원 범위·복잡한 의미적 품질에 따른 실패가 가능하다. 이 변경은 해결 가능한 공통 오류를 수정하는 흐름과 잘못된 공개 차단을 개선한 것이다. 모든 요청의 무오류 성공, 인과/통계 모형 지원, 전체 주제 반복 안정성, 사람의 의미적 품질 승인, 기존 PRD의 외부 환경·파일럿을 완료로 간주하지 않는다. 생성 과제는 계속 시험 과제다.
