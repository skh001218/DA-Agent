# 요청 해석 형식 개선·오류 진단 수집

- 날짜: 2026-10-05 (Asia/Seoul)
- 프롬프트: bounded-planner-v3. 해석 응답: interpretation-v3. 진단: interpretation-diagnostics-v1.
- 범위: 기존 데이터/승인된 접속 규칙의 요청 해석. adaptive 설계, 코칭·평가 기준, 테이블 생성 한도는 이 변경의 대상이 아니다.

## 변경

AI 응답을 analysis_topic, capability_id, difficulty, task_kind, goal, questions, unsupported, reason의 여덟 필드로 고정했다. 모두 필수이며 불필요한 필드·자료형 변환을 허용하지 않는다. 질문이 없으면[], 선택할 능력이 없으면null을 사용한다. Gemini에 application/json과 responseJsonSchema를 적용해 허용 값과 실제 지원 능력 ID를 제한한다. 서버는 길이·필드·자료형·지원 능력과 유형의 일치를 다시 검증한다. 공급자의 스키마 지원이 없는 경로도 같은 프롬프트와 서버 검증을 사용한다.

예시 템플릿: [interpretation-response.json](../template/interpretation-response.json). 요청당 해석3회 상한과 수동 재시도를 유지하며 자동 재시도나 추가 진단 AI 호출은 도입하지 않았다.

이전의 plan_invalid 사용자 오류 코드는 유지하면서 failure_detail에 안전한 상세 원인을 추가했다.

| 단계 | 기록 내용 |
| --- | --- |
| provider | 키·권한·모델·사용량 제한·시간 초과 등 고정된 공급자 오류 코드; 알 수 없는 이유는unknown |
| json | JSON 문법 해석 실패 |
| schema | 허용된 필드 이름과 missing/extra/type/enum/length/constraint. 알 수 없는 추가 필드 이름은unknown_field로 대체 |
| selection | 존재하지 않는 능력 또는 능력과 과제 유형의 불일치 |
| internal | 예상하지 못한 해석 경로 오류. 예외 메시지·입력·스택 지역 변수는 저장하지 않음 |

실패는 quality_events_v2의 ai_finished·generation_finished와 quality_operations에 저장한다. JSON/스키마/선택 실패는format_invalid, 공급자/내부 실패는provider_failure로 구분한다. 모델·프롬프트 버전·발생 시각·요청/작업 ID·경과 시간도 연결한다. 공급자가 토큰 사용량을 반환한 형식 실패는 그 실제 사용량을 보존한다. 요청의 failure_history에도 상세 원인을 보존하고 수동 재시도 시 현재 failure_detail만 비운다.

정상적인 추가 질문 대기는 해석 단계의 완료로 기록하고 실패·validation_failed로 기록하지 않는다. 요청 상태는needs_clarification을 유지하며 실제 출제는 사용자 답변 후 검증·ready까지 진행한다.

진단 데이터는 enum 기반의 제한된 구조로 검증하며 사용자 원문·SQL·결과 행·모델 원문·API 키·잘못된 필드 값·자유 형식 예외 메시지를 추가하지 않는다. 기존 이벤트 보존 정책의 적용 대상이며 새 저장소나 전체 원문 로그를 만들지 않는다. 과거 실패의 원문·필드 오류는 소급 복원할 수 없다.

## 확인 방법

- [최근 진단 기록](http://127.0.0.1:8087/api/quality/diagnostics): 기본 최신30개, limit은1~100. 오류가 아직 없으면 events가 빈 배열이다.
- 특정 요청: `/api/quality/diagnostics?request_id=<요청 ID>`.
- 요청 결과: `/api/training/requests/<요청 ID>`의 failure_detail·failure_history. 요청 결과는 기존의 사용자 요청을 포함하므로 원문 없는 진단 내보내기에는 위 전용 API를 사용한다.
- 하나의 실패에 AI 작업 종료와 출제 작업 종료 두 이벤트가 연결될 수 있다. 같은 요청·작업 ID를 기준으로 읽으며 두 번의 AI 호출로 계산하지 않는다.

예를 들어 `stage=schema, issues=[{field=difficulty,kind=enum}]`이면 난이도 허용 값 불일치이고, `stage=selection, issues=[{field=capability_id,kind=capability_kind_mismatch}]`이면 지원 능력과 유형이 맞지 않은 것이다. 추가 오류가 생기면 이 기록으로 고정 표본을 만들고 해당 검증 단계부터 조사한다.

## 검증

- 정상·오류 단위 검증: JSON, 필수 누락, 자료형, 허용 값, 길이, 추가 필드, 지원 능력/유형, 공급자 이유 구분 및 원문 비수집 확인.
- 실제 DB 오류 주입: 잘못된 응답을 사용해 한 번 실패, 오류 단계·토큰11/7·두 종료 이벤트 저장, 진단API 조회, 수동 재시도ready, 과거 failure_history와 이벤트 보존 확인. 모의 공급자이며 실제 모델 오류를 재현했다고 주장하지 않는다. 사용자 데이터 대신 폐기 가능한 기록 스키마에서 수행.
- 실제 모델·DB·브라우저: 기존 데이터 선택 후 추천 중급 계산 그대로ready, 과제 화면 진입 확인. 요청5daab947-c53b-492b-bc54-3a9924ca8ac7, 검증 훈련18524a4b-f7d5-4ae0-9806-a17bcd7a6a5b 보존. 실행 앱과 워크스페이스의 화면 파일이 다른 것을 확인해 현재 소스로 다시 빌드했다. 이후 [최신 추천 시작 결과](prd-v2-browser-2026-10-05/planner-diagnostics-recommendation.json), [화면](prd-v2-browser-2026-10-05/planner-diagnostics-recommendation.png)에서 추천 중급 설계의 추가 질문→답변→ready를 확인했다. 최신 요청c40ce5af-5c4c-4a83-a6b5-a724d8968fae, 훈련c5871d92-2f82-4a9c-bc5c-c7021a80eacf. 최신 기록이 계산 즉시ready를 가리키는 것으로 해석하지 않는다.
- 최초 전체 DB 회귀는 테스트 응답이 새 필수 questions 필드를 생략해10개 실패·245개 통과했다. [최초 기록](verification-planner-diagnostics-db-initial-2026-10-05.json)을 보존하고 정상 응답 fixture에 questions=[]를 추가했다. 런타임 검증을 완화하지 않았다.
- 갱신 fixture의 전체 DB 회귀255개 통과를 [중간 기록](verification-planner-diagnostics-db-2026-10-05.json)에 보존했다. 이 실행의 화면 파일은 워크스페이스와 달라 source_matches_host=false다. 현재 소스로 다시 빌드 후 [전체 회귀255개 통과·소스 일치](verification-planner-diagnostics-final-db-2026-10-05.json)를 확인했다.
- 마지막 추가 질문 상태 분류 수정 후 요청 해석·오류 진단·추가 질문·취소·재시도·출제의 관련 실제 DB 회귀를 다시 수행했다. 해당 결과는 [마지막 상태 분류 검증](verification-planner-diagnostics-final-state-2026-10-05.json)을 따른다. 전체255개 실행의 소스 버전과 마지막 메타데이터 변경을 구분한다.

실제 추천 한 건의 정상 출제를 모든 요청의 의미적 해석 품질 승인으로 확대하지 않는다. 추후 발생하는 실패를 위 진단 기록으로 수집해 원인별로 재검증한다. 평가 점수 편차와 코칭의 가설 충돌 행동은 기존 남은 작업이다.
