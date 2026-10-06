# Gemma 연결 및 실제 출제 재검증

## 결과

검색·출제 모델을 모두 `gemma-4-26b-a4b-it`로 설정하고 localhost:8087 서비스에 반영했다. 현재 .env의 GEMINI_API_KEY는 유지했고 GEMINI_MODEL/GEMINI_SEARCH_MODEL만 변경했다. 모델은 thinkingLevel=minimal을 사용한다. 원래 컨테이너의 DB·패키지·인증 볼륨을 유지하고 서비스 health 200 및 브라우저의 Gemma API 표시를 확인했다.

실제 세 난이도 테스트는 진행 가능해졌고 검색 근거를 확보했지만, **출제 성공은 0/3**이다. 문제 미생성으로 SQL 검산·저장·재개와 세 난이도 문제의 의미적 품질 비교는 완료하지 못했다.

| 난이도 | 실제 검색 | 선정 결과 | 최종 실패 및 한계 |
| --- | --- | --- | --- |
| 초급 | 질의 5개 / 연결된 출처 chunk 4개 | 구독 서비스 고객 이탈 방지 | 설계 응답에서 필수 reason 누락 2회, 다음 자동 수정에서 HTTP 429 |
| 중급 | 질의 5개 / 출처 chunk 5개 | 선정 미완료 | 처음 복잡한 스키마 응답 미완료, 보완 후 재선정 호출 HTTP 429 |
| 고급 | 질의 4개 / 출처 chunk 8개 | 핀테크 실시간 이상 거래 탐지 | reason 누락 및 생성 그룹 count>500, 자동 수정에서 HTTP 429 |

세 요청 원문은 모두 `실무에서 일어날만한 문제를 분석하고싶어`이며 선택 난이도만 바꿨다. 처음에는 검색 없는 응답을 반환하여 3개 모두 research_unverified로 차단했다. 지시를 보완하여 실제 검색이 수행된 뒤 동일 요청을 재시도했고 검증된 검색 근거를 재사용했다.

요청 ID:
- 초급: f1a60980-4d9b-443d-ada5-ef295a386e4f, 최종 planning_calls=7
- 중급: 85cc8824-79aa-446c-a80f-255880ea155f, 최종 planning_calls=4
- 고급: 76e128a1-a65e-429a-9f7d-5a55d245fe4f, 최종 planning_calls=6

## 연결 보완 및 검사

Gemma가 developer 지시만으로 검색을 실행하지 않아, 사용자 메시지의 JSON 앞에도 실제 검색 실행·근거 인용을 명확하게 요구했다. 검증 기준을 완화하거나 검색 없는 응답을 공개하지 않았다.

간단한 JSON 스키마의 실제 호출은 성공했다. 복잡한 후보 스키마를 API의 responseJsonSchema로 강제한 호출은 HTTP 200이어도 완성 응답을 받지 못했고, 출력 상한을 늘린 별도 진단도 api_timeout으로 실패했다. JSON 형식으로 응답받아 서버에서 동일 Pydantic 스키마를 검사한 비교 호출은 정상 후보 선정에 성공했다. 이에 Gemma의 입력 envelope에 schema가 포함된 출제·선정 호출은 API JSON 모드를 유지하고 responseJsonSchema 강제만 제외한다. 스키마는 모델 입력에 그대로 전달하고 서버의 필수 필드·범위·출처·데이터·품질 검증을 유지한다. Gemini의 기존 스키마 요청은 유지한다.

새 연결 검사 포함 격리 전체 자동 회귀: **320 passed**, 26.34초. API/검색 관련 검사 38 passed, DB 검사 2개는 이 단독 검사에서만 skipped였고 전체 격리 회귀에 포함되어 통과했다. JavaScript 구문과 git diff --check 통과. 테스트용 별도 training 데이터베이스는 삭제했다. 실패한 실제 출제 요청은 원인 확인과 재개를 위해 보존했다.

## 사용 제한과 품질 판단

최종 AI 운영 기록은 초급의 마지막 설계 수정, 중급의 재선정, 고급의 설계 수정에서 모두 provider_diagnostic.http_status=429를 기록한다. Gemma의 검색 지원 성공이 후속 호출의 무제한 사용을 의미하지 않는다. 모든 실패를 응답 형식 오류로만 설명하면 잘못이다.

서비스 반영 후 같은 Gemma로 매우 짧은 일반 생성 요청은 다시 HTTP 200이었다. 따라서 지속적인 키 오류나 모든 요청의 일일 소진으로 확정하지 않았다. 큰 입력·연속 호출에 적용되는 별도 토큰/요청 한도 가능성이 있으나, 실패 호출의 상세 quotaMetric은 이 운영 기록에서 수집되지 않아 정확한 제한 항목은 미확정이다. 작은 호출의 성공만으로 큰 설계 호출도 가능하다고 주장하지 않는다.

출처의 질도 개선이 필요하다. 초급이 선택한 learnhub4u.com의 [사례 페이지](https://www.learnhub4u.com/case-studies)는 교육용 비즈니스 문제 시나리오로 설명되어 있어, 실제 기업에서 발생한 사건의 원출처라고 단정할 수 없다. 검색 질의와 인용 연결 검증을 통과한 것이 실무 사례의 사실성을 승인한 것은 아니다. 실제 기업·기관의 원출처를 확보하는 검토가 남는다. 선정된 초급·고급 주제는 보스 레이드와 다르지만, 0개 출제 결과로 최종 문제 다양성이나 난이도 분리를 승인할 수 없다.

## 실행 환경과 증거

- 실행 이미지: da-agent-app:gemma-worktree-20261006, 이전 이미지: da-agent-app:before-gemma-20261006.
- 기존 프로젝트 .local/gemma-worktree.override.json에 현재 worktree의 build context와 이미지·검색 모델을 저장했다. 이후 재빌드는 기존 compose.yaml과 이 override를 함께 사용해야 현재 출제 코드가 유지된다.
- [전체 요청·검색·선정·검증 진단](../artifacts/gemma-generation-2026-10-06/audit.json)
- [최종 API 결과](../artifacts/gemma-generation-2026-10-06/final-results.json)
- [JSON 모드 비교 호출](../artifacts/gemma-generation-2026-10-06/selection-json-result.json)
- [짧은 일반 호출의 재확인](../artifacts/gemma-generation-2026-10-06/quota-diagnosis.json)
- [서비스 화면](../artifacts/gemma-generation-2026-10-06/service-result.jpg)

## 남은 작업

큰 설계 호출의 한도 확인, Gemma의 필수 필드·행 수 제약 준수 보완, 실제 기업 원출처 확인, 초급·중급·고급의 출제 성공 후 검산·화면·품질 비교. 이번 요청의 연결 및 실제 재검증은 수행했지만, 출제 기능이 안정적으로 작동한다고 승인하지 않는다.
