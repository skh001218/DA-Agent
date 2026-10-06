# Discord 자연어 조회와 의미가 고정된 SQL

## 구현 진행도

- 진행도: 4/4개 완료 (100%)
- 마지막 갱신일: 2026-10-06
- 남은 작업: 조회 엔진의 정의된 검증 작업 없음. 실제 Discord 전달은 Spec022/023에서 검증 대기.
- 차단 사유: 조회 엔진 없음. 실제 Discord 서버 검증은 별도 대기.

| 작업 | 상태 | 완료 조건 | 관련 코드 / 검증 결과 |
| --- | --- | --- | --- |
| 공개 조건 해석 | 완료 | 불명확한 기간·분모를 확인하고 비공개 정보를 모델에 전달하지 않음 | Gemma 실제 요청·확인 답변·지원 밖 차단, 공개 투영·저장 검증 완료 |
| SQL 컴파일 | 완료 | 고유 사용자·도전 수·완료율·D7·주간 재방문을 구분하고 공개 컬럼만 사용 | discord_query.compile_query, 실제 PostgreSQL 고정 결과 대조 |
| 실행 보존 | 완료 | 실제 SQL·오류·미리보기·전체 수집 결과와 제한을 반환 | 실제 SqlRunner 별도 읽기 전용 계정, 미리보기1/전체2행 검증 |
| 의미 검증 | 완료 | 고정 데이터에서 중복·날짜 경계·관측 완료·0 분모 대조 | test_discord_query, verification-discord-query-2026-10-06.md |

## 범위와 계약

PRD 6.2–6.4절의 조회 기능. 기존 웹 모듈을 변경하지 않는 별도 `DiscordQueryEngine(provider, runner, settings)`를 사용한다.
`resolve(text, public_task, previous_conditions=None, clarification=None, difficulty='intermediate')`는 `clarification / ready / error`를 반환한다. `execute(session_id, schema_name, plan, public_task)`는 실행 SQL·시도 이력·미리보기와 즉시 `runner.get`으로 복사한 전체 수집 결과를 반환한다. 서비스 계층이 소유권 확인 및 영구 저장을 수행한다.

대표 스키마는 users(user_id, signup_date, channel), tutorial_attempts(user_id, step, completed, attempt_at), sessions(user_id, session_at)이다. 지원 지표는 users_count, attempts_count, tutorial_rate, d7_retention, weekly_return이며 임의 SQL 생성은 지원하지 않는다. 모델은 조건을 해석하고 SQL은 검증된 고정 연산으로 컴파일한다. 지원 밖의 요청은 실행하지 않는다.

조건은 지표·반개구간(start 포함, end 제외)·시간대·단위·분자/분모·필터·그룹 및 필요 시 step, baseline_start/end로 구성한다. 이전 조건 재사용은 모델의 명시적 reuse_previous에 한정한다. 중급·고급 확인은 중립 질문, 초급 선택지는 개념 힌트로 기록한다. API 실패나 잘못된 응답을 가상의 조회 결과로 대체하지 않는다.

D7은 가입 날짜+7일의 접속이며 관측 종료 미만인 완료 코호트만 분모에 넣는다. 주간 재방문은 첫 기간 고유 접속자 중 두 번째 기간 접속자의 비율이다. 완료율은 기간·단계의 도전 고유 사용자 중 완료를 한 고유 사용자의 비율이며 재도전 중복을 제거한다. 분모 0은 NULL 비율로 반환한다. 시간대는 공개 과제 계약과 정확히 일치해야 한다.

대표 과제의 기본 완료율 분모는 기간 내 신규 가입 고유 사용자(signup_users)이며 attempted_users를 선택한 경우에만 같은 가입 코호트의 도전 사용자로 제한한다. 단계3 반복 시도 수(duplicate_attempts)는 재도전을 포함한 같은 사용자·단계의 추가 이벤트 수이며, 수집 중복 여부의 확정 결과가 아니다. 공개 사전·목적·지표 정의·인정 한계를 모델에 전달한다. 부분 해석은 proposed_conditions로 반환하며 확정 기준과 구별한다.

컴파일은 같은 의도의 수정이 필요 없도록 유한 연산을 사용한다. 연결/직렬화/교착 같은 일시 오류만 같은 SQL로 1회 재실행하며 차단·문법·시간 초과는 재실행하지 않는다. 모든 시도와 실제 runner 오류를 보존한다. 성공 후 전체 결과 회수 실패는 저장 실패로 표시하고 성공 근거로 취급하지 않는다.
