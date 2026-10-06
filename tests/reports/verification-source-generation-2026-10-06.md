# 검색 근거 기반 출제 검증 · 2026-10-06

## 구현 결과

새 adaptive 출제는 실제 Google Search 호출 → grounding 질의·인용·출처 확인 → 실무 사례 후보 비교 → 난이도별 합성 과제 → 독립 Python/PostgreSQL 검산의 흐름으로 변경했다. 실제 검색 질의 또는 출처에 연결된 응답 문장이 없으면 문제를 공개하지 않는다. 추상 요청은 3개 이상의 서로 다른 업무 후보를 요구하고 최근 5개 과제는 반복 회피 자료로만 전달한다. 특정 분야/주제를 직접 요청하면 유지한다. 이전의 보스·스테이지·장비 기본 유도를 제거했다.

검색 근거·선정 이유·검색 시각을 요청별 고정한다. 새 과제의 서버 공개 계약에는 source_case가 포함되고 기존 과제에는 선택 사항이다. 화면에서 링크와 인용 근거를 볼 수 있으며 실제 공개 사실과 가상 업무 조건·합성 수치를 구분한다. 검색 실패를 기존 보스 문제로 대체하지 않는다. 반복 후보만 남아 실패한 요청은 다음 재시도에서 다시 검색한다. 검색/선정 2회가 추가되어 모델 호출 상한은 8회다. 선택적 GEMINI_SEARCH_MODEL 설정은 비어 있으면 기존 생성 모델을 사용한다.

Gemini generateContent의 googleSearch 도구 및 groundingMetadata 계약은 [공식 API 문서](https://ai.google.dev/api/generate-content)를 확인했다. 검색 제공자의 검색 제안 HTML은 스크립트를 허용하지 않는 sandbox iframe에 표시한다.

## 자동 검증

- 별도 `case_verify_<UUID>` training 데이터베이스와 임시 패키지, `verify_request_<UUID>` recorder schema를 사용했다. 사용자 패키지 및 사용자 기록을 덮어쓰지 않았다.
- 기본 training-001/v1,v2 패키지를 테스트 환경에 생성/검증한 뒤 전체 자동 검사: **319 passed**, 26.38초. 기존 라이브러리 deprecation 경고 2건.
- 새 검색 계약 검사는 실제 검색 도구 포함 여부, grounding 추출, 가짜 출처 ID·없는 질의·없는 근거·private URL·잘못된 인용 인덱스 거부, 넓은 요청의 후보 수, 새 주제 우선, 반복 후보 실패를 확인했다.
- 실제 DB 검사는 검색 실패 시 attempt 미생성, 같은 요청 재시도, 공개 계약의 출처 및 source_topic 보존, 검색 중 취소 시 후보 선정 미호출을 확인했다.
- JavaScript app.js/training.js 구문 검사 및 git diff --check 통과.

## 브라우저 검증과 실제 모델 제한

모의 검색/선정/생성 응답을 사용하는 별도 8089 서버에서 실제 DB와 브라우저를 연결했다. 화면 검증 요청은 `실무에서 일어날만한 문제를 분석하고싶어`, 중급이다. 모의 출력은 교육 품질이나 실제 검색 성공의 증거가 아니다. example.org URL과 사례 설명은 테스트 계약을 위한 고정 입력이며 실제 업무 근거로 사용하지 않았다.

- 출처 영역의 선정 주제·링크·인용·검색 시각·선정 이유·합성 조건 안내 확인.
- ‘업무 조건 확인’을 누른 후에도 출처 영역 유지 확인.
- `SELECT count(*) AS n FROM accounts` 실제 실행 성공, 60행.
- 기록 저장 완료 확인. 새 브라우저 탭으로 같은 attempt를 열어 저장 근거와 출처 복원 확인.
- 브라우저 증거: [출처 화면](../artifacts/source-generation-2026-10-06/mock-sources.jpg), [새 접속 복원](../artifacts/source-generation-2026-10-06/mock-resumed-sources.jpg).

실제 Gemini 검색은 기본 모델과 별도 검색 모델 모두 HTTP 429 RESOURCE_EXHAUSTED를 반환했다. 제공자 응답은 현재 키의 할당량 초과를 명시했다. 따라서 실제 검색 성공, 실무 근거와 생성 과제의 의미적 정합성, 세 난이도의 최종 품질은 확인하지 못했다. 할당량을 소진하는 반복 호출을 계속하지 않았다.

8088 실제 UI 요청 `2219880e-86d7-4252-b75a-7120710b3d75`는 요청 접수 → 실무 사례 검색 → 출제 실패를 표시하고 입력·재시도를 보존했다. [실제 실패 화면](../artifacts/source-generation-2026-10-06/live-search-failure.jpg). 이후 한도 오류와 API 키/권한 오류의 안내 문구를 구체화했으며 자동 회귀를 다시 통과했다.

## 실행 서비스 반영

현재 worktree 소스로 이미지를 빌드하여 기존 localhost:8087의 app 서비스만 교체했다. 기존 DB·패키지·인증 볼륨을 유지했고 상태 확인 API는 200이다. 실행 이미지에서 호출 상한 8과 검색 지원 메서드를 확인했다. 이전 이미지는 `da-agent-app:before-source-generation-20261006` 태그로 보관했다. 반영 후 실제 실패 요청이 8087 화면에서도 복원됨을 확인했다.

Compose의 기존 프로젝트 경로는 Desktop/수업자료/DA-Agent이다. 현재 반영 이미지는 이 worktree에서 빌드했으므로, 이후 기존 Desktop 소스를 그대로 다시 빌드하면 이번 변경이 제외될 수 있다. 향후 빌드는 변경된 worktree 소스를 기준으로 해야 한다.

## 남은 확인

API 할당량이 복구되면 동일한 추상 요청으로 초급·중급·고급을 실제 출제하여 주제 다양성, 출처와 상황의 정합성, 난이도 차이, 지표/검산/평가 정합성을 확인해야 한다. 자동 구조·기능 검증을 실제 모델의 의미적 품질 승인으로 간주하지 않는다.

## 사용자 요청에 따른 실제 검색 재검증 · 2026-10-06 11:19 KST

이전에 할당량 초과로 확인하지 못한 실제 검색과 세 난이도 출제만 다시 검증했다. 완료된 자동 회귀와 모의 화면 검사는 반복하지 않았다.

- 실행 서비스: localhost:8087, 실제 Gemini 연결.
- 요청: `실무에서 일어날만한 문제를 분석하고싶어`, 초급, 기본 검색 모델 `gemini-3.5-flash-lite`.
- 요청 ID: `3a5d6845-f9e8-40d3-bde7-95ace0b2aba9`.
- 실제 검색 1회, 제공자 HTTP **429** 재발. 운영 기록에서 `prompt_version=case-search-v1`, `provider_diagnostic.http_status=429`, `timeout=false` 확인. 검색 호출 324ms, 전체 작업 438ms.
- 결과: `research_failed`, `attempt_id=null`, `planning_calls=1`, 요청과 재시도 유지. 사례 선정 및 문제 생성 단계에는 도달하지 않았다.
- 동일한 검색 연결이 첫 단계에서 막혀 중급·고급과 주제 다양성·근거 정합성·난이도별 품질 비교는 실행하지 않았다. 현재 Spec019의 차단 상태와 80% 진행도를 유지한다.
- API 응답 증거: [재검증 결과 JSON](../artifacts/source-generation-live-recheck-2026-10-06/results.json).

## 추가 재검증 · 2026-10-06 11:26 KST

사용자가 다시 검증을 요청하여 실제 검색을 1회 재호출했다. 동일한 추상 요청의 초급 요청 `75a2350c-aa4e-470f-a891-c778f28eb019`가 `case-search-v1` 단계에서 다시 실패했다. 제공자 운영 기록의 HTTP 429를 확인했다. 문제 미생성(`attempt_id=null`, `planning_calls=1`)으로 중급·고급 추가 호출 및 의미적 품질 비교는 진행하지 않았다. 완료된 회귀 검사는 반복하지 않았고 Spec019의 80%·차단 상태를 유지한다.

- [요청 결과](../artifacts/source-generation-live-recheck-20261006-112648/results.json)
- [실제 제공자 진단](../artifacts/source-generation-live-recheck-20261006-112648/provider-diagnostics.json)

## .env 키를 직접 사용한 재검증 · 2026-10-06 11:36 KST

사용자 요청에 따라 실행 프로젝트 `C:/Users/Administrator/Desktop/수업자료/DA-Agent/.env`의 `GEMINI_API_KEY`를 직접 읽어 검사했다. 현재 서비스의 secret 파일과 같은 값임을 값 출력 없이 확인했다. 키는 프로세스 표준 입력으로만 전달하고 일회성 컨테이너의 권한 0600 임시 파일에서 사용했으며 테스트 종료 시 제거했다. 로그·증거에는 키를 기록하지 않았다.

현재 app 컨테이너는 11:34 KST에 재생성되었으며 `case_research` 모듈과 `GeminiProvider.research` 메서드가 없는 실행 이미지다. 그래서 기존 서비스를 수정하지 않고 현재 worktree의 src를 일회성 테스트 컨테이너에 읽기 전용 마운트해 수정된 검색 코드로 호출했다. 초급의 동일한 추상 요청으로 실제 검색 1회 결과는 `api_rate_limited`, HTTP **429**, 모델 `gemini-3.5-flash-lite`, timeout=false다. 따라서 .env 키로도 검색은 차단되며 세 난이도 출제/품질 검증은 진행하지 못했다.

- [직접 호출 결과와 제공자 진단](../artifacts/env-key-search-recheck-20261006-113631/result.json)
- 현재 서비스에는 검색 코드가 제외되어 있으므로, 앞선 ‘실행 서비스 반영’은 이전 배포 시점의 기록이다. 이번 요청은 키 테스트이므로 서비스를 재배포하거나 키 설정을 바꾸지 않았다.

## 429 원인 분리 검사

사용자가 원인을 요청하여 같은 .env 키와 `gemini-3.5-flash-lite`, 같은 최소 프롬프트(`Reply with OK.`), 최대 출력 16토큰으로 요청을 비교했다. 일반 generateContent는 HTTP **200**으로 성공했다. `tools=[{googleSearch:{}}]`만 추가한 요청은 HTTP **429 RESOURCE_EXHAUSTED**로 실패했다. 따라서 현재 재현되는 제한은 Google Search 사용 요청에 연결되어 있다. 키 오류나 일반 생성 전체 사용 불가는 아니다. 이전의 ‘전체 API 할당량 초과’ 설명은 검색 사용 제한으로 범위를 좁혀 정정한다.

실제 검색 오류의 details에는 Help 링크만 있고 QuotaFailure의 quotaMetric/quotaValue, RetryInfo, Retry-After가 없다. 응답만으로 검색 한도가 0인지, 분당/일일 검색 소진인지, 결제 등급 제한인지 확정할 수 없다. Google 공식 [가격표](https://ai.google.dev/gemini-api/docs/pricing)의 Gemini 3.5 Flash-Lite 항목은 일반 생성은 무료 등급에서 가능하지만 Grounding with Google Search는 무료 등급에서 Not available이라고 명시한다. 따라서 무료 프로젝트의 검색 사용 자격 부재가 유력한 원인 후보지만 실제 프로젝트 등급·할당량은 인증된 AI Studio 계정에서 확인해야 한다. 유료 프로젝트의 별도 검색 한도 소진 가능성도 남는다.

- [일반 생성 결과](../artifacts/env-key-search-recheck-20261006-113631/quota-diagnosis.json)
- [검색 추가 요청의 원문 오류](../artifacts/env-key-search-recheck-20261006-113631/quota-diagnosis-search.json)
