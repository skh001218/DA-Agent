# Gemma API 사용 조사 및 실제 호출

- 사용자 요청: Gemma를 API로 사용하는 방법을 알아본다. 서비스 설정을 변경하거나 전체 출제 흐름을 구현하는 요청은 아니다.
- 공식 문서: [Gemma on Gemini API](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api). 현재 명시 모델은 gemma-4-26b-a4b-it, gemma-4-31b-it. 동일한 Gemini API endpoint와 AI Studio API 키를 사용한다. 시스템 지시 및 Google Search 예제를 제공한다.
- [가격표](https://ai.google.dev/gemini-api/docs/pricing)는 Gemma 일반 생성의 무료 등급과 검색 Not available을 명시하여, 위 사용 문서와 검색 안내가 일치하지 않는다. 계정별 실제 호출 결과와 문서의 한계를 구분한다.

## 실제 검사

현재 실행 프로젝트 .env의 GEMINI_API_KEY를 표준 입력으로 일회성 컨테이너에 전달했다. 키를 출력·저장하지 않았다. 실행 서비스 또는 .env는 변경하지 않았다.

1. gemma-4-26b-a4b-it, thinkingLevel=minimal, 일반 생성: HTTP 200, OK. 반환.
2. 같은 최소 요청에 googleSearch 도구 추가: HTTP 200. 이 요청은 검색이 필요 없는 OK 요청이므로 실제 검색 성공의 증거로 간주하지 않았다.
3. 공식 Gemma 모델 목록을 Google 검색하여 인용하도록 요청: HTTP 200, 실제 webSearchQueries 2개와 groundingChunks/groundingSupports 반환. 프로젝트 grounded_sources 검증 통과. 반환 chunk 2개는 동일 URL로 별도 독립 출처 2개라는 의미는 아니다.

- [최소 호출 결과](../artifacts/gemma-api-probe-2026-10-06.json)
- [실제 검색과 인용 근거](../artifacts/gemma-search-probe-2026-10-06.json)

## 프로젝트 적용 판단

Gemma API 호출과 실제 검색의 가능성을 확인했다. GEMINI_MODEL 또는 GEMINI_SEARCH_MODEL에 Gemma 모델을 지정하는 연결 후보가 된다. 다만 출제용 responseJsonSchema 호환성, 긴 응답과 thinking의 토큰 상한, 후보 선정의 의미적 품질, 난이도별 실제 출제는 아직 검사하지 않았다. 기존 서비스는 현재 검색 코드가 없는 이미지이므로 환경 변수 변경만으로 전체 흐름이 복구된다고 주장하지 않는다. 추후 구현 시 변경된 출제 코드를 포함한 이미지 배포와 실제 세 난이도 검증이 필요하다.
