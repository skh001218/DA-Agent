# Spec035 텍스트 요청 기반 출제 구현과 검증

- 확인일: 2026-10-06 (Asia/Seoul)
- 작업 폴더: 현재 548e worktree
- 진행도: 5/7개 완료(71.43%). 코드·서비스·임시 DB 검증은 완료, 운영 명령 동기화는 검증 대기, 실제 모델·Discord 전체 검증은 차단.
- 실제 운영 봇은 D:/Codex/DA-Agent의 별도 이미지로 실행 중이다. 기존 봇·웹·사용자 DB를 재시작하거나 변경하지 않았다.

## 구현 결과

`/training`은 필수 text와 기존 difficulty·help_level을 받는다. 공개 topic choices를 제거하고 text를 RequestV2.message로 전달한다. 내부 legacy 시작 함수는 기존 테스트·기록 호환에 남았지만 새 슬래시 명령에서 text 누락을 고정 문제로 대체하지 않는다.

discord_generation.py는 기존 adaptive·case_research·task_quality의 설계·수정·적합성 검토와 stage_adaptive의 합성 자료·독립 PostgreSQL 검산을 재사용한다. stage/grant/revoke에 명시적 관리자 DSN·읽기 역할 인수를 추가했으며 기존 웹 호출의 기본값은 유지한다. 선언형 설계 밖 코드·모델 SQL은 실행하지 않는다.

요청 상태는 분석 시작과 구분해 영구 저장한다. 비공개 checkpoint는 별도 generation_jobs에 저장하고 소유권을 검사한다. 확인 질문은 /answer·기존 답장으로 연결하며 /end 취소, /resume 조회, 재시도 버튼·/retry 실행을 제공한다. 동일 이벤트 중복·다른 입력 충돌·호출 상한·취소 후 늦은 응답 미공개·고정 계획 재시도를 확인했다. 생성 완료 후 과제 제목으로 스레드 이름을 갱신하도록 연결했다.

생성 자료의 자연어 조회는 공개 표·컬럼·FK로 검증한 조건을 기존 analytical_metrics 컴파일러에 전달한다. count/distinct/sum/avg/min/max/ratio, many-to-one 조인, 최대3차원 그룹을 지원한다. ratio는 조건을 만족하는 행 비율이며 복합 고유 사용자 비율·표준편차·분산은 지원 밖이다. 전체 조회 결과·SQL·실행 ID·자료 버전을 저장 근거로 연결한다.

교육·평가에는 공개 요구사항을 기존 Discord 항목으로 변환해 고정한다. tutorial 전용 안내·검산을 생성 과제에 적용하지 않는다. 임의 자연어 수치 전체의 자동 검산은 미지원으로 명시하며 미검산을 감점으로 만들지 않는다. 반복 품질 프로필 미등록이면 총점을 보류한다. 평가 입력과 프로필이 같은 PUBLIC_TASK_KEYS를 사용하도록 통합하고 제목·accepted_limits·valid_paths·quality_information 변경에도 승인 재사용을 차단했다.

검색은 DISCORD_SEARCH_MODEL을 명시해야 하며 미설정 시 구체적인 설정 안내를 저장한다. 자동 모델 대체는 없다. Compose에 생성 패키지 영구 볼륨을 추가했다.

## 자동 및 실제 DB 결과

| 검사 | 결과 | 범위 |
| --- | --- | --- |
| 최종 전체 pytest | 549 passed, 72 skipped | src 기준 전체 회귀. 환경 조건 때문에 생략된 테스트는 통과로 계산하지 않음 |
| Discord·평가 관련 회귀와 임시 DB | 128 passed, 6 skipped | 전용 records/data DB의 기존 기록·보고·평가와 새 출제·조회·품질 지문 회귀. 별도 환경을 요구하는 조회 검사6개는 생략 |
| 신규 생성 전체 서비스 | 성공 | 고정 모델 응답 → 설계 검사 → 실제 적재·검산 → FK 그룹 평균 → 근거 → 보고·후속 답변 → 제출·총점 보류 → 재시작 후 복원 |
| 읽기 권한 | 쓰기 차단 확인 | 전용 spec035_reader 역할의 INSERT가 InsufficientPrivilege |
| 정적 검사 | 통과 | compileall, git diff --check, Compose config 검사 |

DB는 이 작업에서 만든 da-agent-spec035-test-db 임시 PostgreSQL17.4 컨테이너의 spec035_records/spec035_data를 사용했다. 운영 DB의 스키마·기록·권한을 변경하지 않았다. 서비스 검증의 모델 응답은 고정 fixture이므로 실제 생성 품질 성공으로 계산하지 않는다.

실행 도구: [verify_discord_text_generation.py](../../scripts/verification/verify_discord_text_generation.py). 도구는 명시한 임시 DB 포트에서 실행하며 --live에는 키 파일·검색 모델을 별도로 지정한다. 검증용 --serve 화면은 운영 제품 화면이 아니다.

## 검증용 화면

In-app Browser에서 http://127.0.0.1:8096의 로컬 검증 화면을 조작했다. 임시 실제 DB와 고정 모델 응답을 사용한다.

- 빈 text 요청: 1~4000자 입력 안내, 훈련 미생성.
- 반복 행동 계정 비교 text 요청: analysis 상태로 전환, accounts/activity_daily 공개 자료 준비.
- 플랫폼별 평균 활동량 조회: 두 행의 완전 결과와 실행 ID 저장.
- 보고 저장: followup 상태와 담당자 질문 표시.
- 후속 답변·제출: 보고·평가1개 보존, 반복 품질 프로필 미등록으로 total=null 보류.
- 새로고침: 동일 세션의 조회1·보고1·평가1개 복원. 보류 보고의 재제출도 새 평가를 만들지 않음.
- 브라우저 오류 로그: 빈 배열.

이 화면 확인은 실제 Discord 메시지 전달·버튼·서버 권한·명령 동기화를 대신하지 않는다. 화면과 서비스 기록은 [화면 이미지](../artifacts/spec035-2026-10-06/screen.png), [화면 세션](../artifacts/spec035-2026-10-06/screen-session.json)에 저장했다.

## 실제 모델 호출과 남은 작업

실제 키 파일을 서버 측에서 사용해 명시적인 gemini-3.5-flash-lite 사례 검색을1회 요청했다. 첫 검색이 api_rate_limited(API429)로 실패했다. 과제는 failed로 저장했고 자료를 적재하거나 공개하지 않았다. 같은 제한 호출을 반복하지 않았다. [실패 기록](../artifacts/spec035-2026-10-06/live-7b8b65e3.json).

실제 두 주제·초급/중급/고급 생성, 모델의 교육 품질, 실제 Discord 입력·출제·조회·보고·재개·재시도 버튼은 미확인이다. 운영 반영 후 참가자가 먼저 확인할 행동은 부모 채널의 `/training text:튜토리얼 완료율 하락을 분석하고 싶어 difficulty:intermediate`에서 생성 결과 또는 구체적인 검색 실패 안내가 비공개 스레드에 나타나는지 확인하는 것이다.

운영 반영에는 현재 코드의 이미지 빌드, 검색 모델 설정과 할당량 확인, 기존 볼륨을 보존한 재시작, 서버 명령 동기화가 필요하다. 기존 topic 명령에 새 text 옵션이 이미 등록됐다고 표시하지 않는다. 이 제한으로 Spec035는 100%로 올리지 않았다.

## 증거

- [전체 자동 테스트 XML](../artifacts/spec035-2026-10-06/all-tests.xml)
- [Discord DB 회귀 XML](../artifacts/spec035-2026-10-06/discord-db-tests.xml)
- [생성·조회·제출·재개 서비스 기록](../artifacts/spec035-2026-10-06/integration.json)
- [검증용 화면 소스](../artifacts/spec035-2026-10-06/preview.html)

## Gemma 직접 출제 변경 (후속 요청)

- 사용자 요청: 다른 컴퓨터처럼 Gemma API를 사용하도록 변경.
- DiscordGemmaProvider.generation_mode=synthetic로 지정하고 실제 사례 검색·선정 호출을 생략했다. DISCORD_MODEL의 Gemma로 요청과 난이도에서 직접 가상 과제를 설계·검토한다. Google Generative Language API 전송 코드는 재사용하지만 Gemini 모델을 선택하지 않는다.
- DISCORD_SEARCH_MODEL/GEMINI_SEARCH_MODEL이 설정되어 있어도 Discord에서는 사용하지 않는다. 자동 모델 대체는 없다.
- 공개 sources=[], searched_at=null. 안내에 가상 분석 문제와 검색 미수행을 명시한다. 이전 검색 실패 요청은 Gemma 직접 설계로 전환하고 기존 가상 설계의 고정 계획 재시도는 추가 모델 호출 없이 유지한다.
- 전체 회귀: 552 passed, 72 skipped (`gemma-tests.xml`). 최종 Gemma·생성·전송 회귀 59 passed. compileall, git diff --check 통과.
- 실제 임시 DB의 고정 응답 흐름: 2회 설계/검토, analysis 진입, FK 집계·보고·후속 답변·제출 점수 보류·재시작 복원·쓰기 차단 확인 (`gemma-fixture/integration.json`).
- 테스트 컨테이너 재시작 후 포트가 51919로 바뀌었다. 초기 잘못된 이전 포트 연결은 API 호출 전 중지했고 수정된 포트에서 검증했다. 운영 DB·봇은 변경하지 않았다.
- 실제 Gemma 출제 호출: gemma-4-26b-a4b-it로만 2회 호출. 첫 설계 응답 성공(입력 6152/출력 3161 토큰), 공개 컬럼/분류에 비공개 탐지 라벨이 포함되어 수정 요청. 두 번째 응답은 provider_invalid_json으로 거부하여 실패 상태 저장. 실제 API 연결 성공과 완성된 출제 성공은 구분한다. 오류에서 모델 자동 변경 없음. 기록: gemma/live-d5e02b80.json. 실제 모델 출제 전체·난이도별 품질 확인은 미완료.

- 검증 화면: 과거 고정 과제와 동일한 구조 반복 시 품질 검사로 거부됨을 확인. 검증용 소유자를 실행마다 분리한 뒤 새 text 입력 → analysis 준비 완료 및 Gemma 가상 문제·검색 미수행 안내를 실제 브라우저에서 확인 (`gemma-screen/screen.png`, session 7ed985eb-4bde-4748-b79a-ff7091d94537). 이 화면은 고정 모델 응답을 사용하며 실제 Gemma 출제 성공의 근거가 아니다.

## API 실패 재테스트 (사용자 요청, 2026-10-06)

- 코드나 프롬프트를 변경하지 않고 동일한 모델(`gemma-4-26b-a4b-it`), 요청(`반복 행동 계정의 활동량과 정상 반례를 비교하고 싶어`), 난이도(`intermediate`)로 새 검증 요청을 실행했다. 이전 기록은 보존했다.
- 결과: **실패 재현**, `provider_invalid_json`, 출제 호출 2회. 429·인증·타임아웃 오류는 발생하지 않았고 Gemma API 응답을 받았다.
- 첫 응답: JSON 수신 후 설계 계약 위반(`tables[1].groups[0].count > 500`)으로 수정 요청. 입력 6152 / 출력 2941 토큰.
- 수정 응답: 유효한 JSON 객체로 처리하지 못해 `provider_invalid_json`으로 실패 보존. 입력 8487 / 출력 3184 토큰. 다른 모델로 전환하지 않았다.
- 문제 공개·데이터 적재/검산·재시작 복원에는 도달하지 못했다. API 연결 성공을 출제 성공으로 계산하지 않는다. Spec 진행도는 5/7(71.43%) 유지.
- 운영 봇·운영 DB·명령 등록은 변경하지 않았다. 검증 후 임시 DB 컨테이너를 정지한다.
- [재테스트 결과](../artifacts/spec035-2026-10-06/gemma-retest/live-00963432.json), [호출·실패 요약](../artifacts/spec035-2026-10-06/gemma-retest/api-calls.json).

## JSON 오류 원인 진단과 재현 (2026-10-06)

### 진단 보완

- DiscordGemmaProvider에 JSON 파싱 오류 종류·행/열·문자 위치·길이·SHA-256·최상위 자료형을 기록했다. 구문 오류와 단일 객체 계약 위반을 구별한다.
- 원문 저장은 선택 설정(`DISCORD_JSON_DIAGNOSTICS_DIR` 또는 검증 스크립트 `--diagnostics-dir`)이며, 이번 실행은 Git 제외 `.local/diagnostics/spec035-gemma-2026-10-06`에 저장했다. API 키·HTTP 인증 헤더·입력 메시지는 저장하지 않고 원문을 Discord 응답이나 공개 아티팩트에 넣지 않는다. 원문에는 비공개 문제 설계가 있으므로 서버 전용 보관한다.
- private generation_jobs.calls에 진단 식별자와 오류 정보를 연결하고 사용자 세션에는 노출하지 않는다. 진단 저장 실패가 원래 출제 실패를 덮지 않도록 처리했다.
- 검증: 전체 555 passed, 72 skipped. 추가 비공개 작업 저장·실제 재현 배열 회귀 포함 관련 테스트 32 passed. compileall·git diff --check 통과.

### 동일 조건 실제 Gemma 테스트

- 모델 gemma-4-26b-a4b-it, 요청 '반복 행동 계정의 활동량과 정상 반례를 비교하고 싶어', 난이도 intermediate. Gemma만 2회 호출. 첫 JSON은 그룹 count>500으로 수정 요청했다.
- 수정 응답 HTTP 200, finishReason=STOP. 응답은 길이 9070자의 **유효한 JSON 배열**이며 dict 객체 1개를 포함했다. 형태는 `[{...}]`였고 호출 계약은 단일 `{...}`이다. json.loads는 성공했고 isinstance(parsed,dict)가 false여서 `provider_invalid_json`을 반환했다. **이번 실패의 직접 원인은 문법 오류가 아닌 최상위 자료형 불일치**다.
- 실제 요청 설정은 responseMimeType=application/json, maxOutputTokens=8192, thinkingLevel=minimal이며 responseJsonSchema가 없다. api_provider.py는 Gemma의 중첩 스키마 디코딩 제약 대응으로 responseJsonSchema를 제거한다. JSON MIME 지정만 남아 배열 반환을 제한하지 못하는 것이 확인된 구현 요인이다.
- API 실패·출력 한도 종료·Markdown fence 처리 오류는 이번 재현 원인이 아니다. 정확한 응답은 비공개 진단 ID bc50f5f99f75430ab9c6101e5e710353에 보존했다. 이전 실패는 원문이 없으므로 모두 같은 배열이었다고 단정하지 않는다.
- [실제 실행 결과](../artifacts/spec035-2026-10-06/gemma-diagnosis/live-773923fb.json), [원문 없는 진단 요약](../artifacts/spec035-2026-10-06/gemma-diagnosis/diagnosis-summary.json), [전체 회귀 XML](../artifacts/spec035-2026-10-06/json-diagnostic-tests.xml).
- 상태: 실패 원인 확인 완료, 출제 실패 자체는 유지. 이번 요청은 진단과 재테스트이며 응답 배열을 자동으로 풀어 검증을 우회하지 않았다. 향후 수정은 루트 객체 반환 제약 강화 또는 명시적인 단일 객체 배열 정규화 정책을 정한 뒤 전체 Recipe·품질·DB 검증을 그대로 적용해야 한다. 운영 봇·DB는 변경하지 않았다.

## 해결 순서에 따른 수정 및 재검증 (2026-10-06)

1. **배열/객체 계약 수정 완료**: 단일 dict 한 개 배열만 객체로 정규화하며, 나머지 배열은 거부한다. Recipe·품질·DB 검사는 유지한다. 재시도는 같은 요청의 비공개 실패 초안과 현재 검증 오류를 전달하며 호출 한도를 유지한다.
2. **FK 관계 처리 보완 완료**: 원본 FK를 그대로 복사한 파생 group_key에만 관계를 인정한다. 일반 숫자·aggregate 키는 거부한다. 실제 PostgreSQL에서 FK 제약 1개 생성, 실제 행 일치, 원본 집계와 Python 요약 일치, 그룹 FK SQL/Python 검산을 확인했다. 증거: derived-fk-db.json.
3. **표본 실패 원인 확인 및 피드백 보완**: 실제 모델이 계정 2행·행동 로그 1행을 설계했고 wait_seconds 범위는 1~3600, 조건은 <10이었다. 고정 데이터에서 일치 행은 0이었다. 다음 수정은 비교 그룹이 하나뿐이었다. 실제 sample_rows/matching_rows/conditions를 비공개 수정 요청에 전달하고 표본·조건별 사례/정상 반례·두 비교 그룹을 생성하도록 안내했다. 데이터를 임의로 추가하거나 품질 검사를 완화하지 않았다.
4. **실제 Gemma 재검증 실패**: gemma-fix-final/live-d3446f8e.json은 동일 요청 총8회에서 비교 그룹 오류 후 HTTP500으로 종료. 프롬프트 보완 후 새 검증 요청은 gemma-fix-samples/live-5fb4da3c.json에서 429, 같은 요청 재시도 live-b6edb970.json 및 live-1042a3ca.json은 연속 HTTP500. 최신 요청 총3회. API 연결과 완성된 출제 성공은 구분한다. API 내부 500 원인은 아직 미확정이며 키 인증 오류라고 단정하지 않는다. 호출 증거는 gemma-fix-samples/api-calls.json, 비공개 원문·정답·키는 제외했다.
5. **운영 배포·명령 동기화·실제 Discord는 차단 유지**: 실제 Gemma 출제·적합성·DB 검산 통과를 선행 조건으로 진행한다. 운영 이미지 백업 및 비공개 DB dump와 Compose override는 준비했으나 운영 봇 교체·메시지 전송은 하지 않았다. 실제 API 성공 이후 두 요청/세 난이도와 Discord 전체 흐름을 확인해야 한다.

최신 자동 검증: 전체 564 passed, 72 skipped (final-fix-tests.xml), 관련 50 passed/4 skipped. 실제 DB 파생 FK 검증 통과. Spec035 5/7(71.43%) 유지. 429 진단은 제공된 quotaMetric/quotaId/quotaValue/retryDelay만 보관하도록 보완했다. 상위 API 오류의 내부 원인 또는 할당량 종류가 응답에 없으면 추측하지 않는다.
