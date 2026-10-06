# Gemini API로 프로토타입 테스트

2026-10-03 사용자는 OpenAI 비용 우려로 Gemini API를 선택했다. 현재 로컬 서버의 제공자를 Gemini로 설정하며 기존 ChatGPT 로그인 코드는 명시적으로 선택할 수 있도록 보존한다. 자동 제공자 전환·자동 재시도는 하지 않는다.

## 키 설정

프로젝트 루트 `.env`에 다음 항목을 넣어 설정할 수도 있다. 실제 키는 로컬 파일에만 입력한다.

```dotenv
DA_LLM_PROVIDER=gemini
GEMINI_API_KEY=발급받은_키
```

Docker Compose는 `.env`의 값을 앱 환경변수로 전달한다. `GEMINI_API_KEY`가 비어 있지 않으면 키 파일보다 우선 사용하며, 비어 있으면 기존 `.local/gemini_api.key`를 사용한다. 환경변수 키의 인증이 실패해도 다른 키로 자동 전환하지 않는다. `.env` 수정 후 `docker compose up -d --build --force-recreate app`으로 적용한다. Python을 직접 실행하는 경우 `.env`는 자동으로 읽지 않으므로 실행 환경에 변수를 설정하거나 기존 키 파일을 사용한다.

기존 숨김 입력 도구 `scripts/setup_api.py`를 실행하면 새 키를 파일에 저장하고 `.env`의 `GEMINI_API_KEY`를 비워 새 파일을 사용하도록 한다.

`.env`는 Git과 Docker 이미지에서 제외한다. `docker compose config`나 컨테이너 환경 전체 출력에는 키가 포함될 수 있으므로 해당 출력을 공유하지 않는다.

[Google AI Studio](https://aistudio.google.com/apikey)에서 Gemini API 키를 발급한다. 무료 티어로 시험하려면 해당 프로젝트의 결제 연결 여부와 실제 사용 한도를 AI Studio에서 확인한다. 코드가 키만 보고 무료/유료 프로젝트인지 판별할 수는 없다.

프로젝트 루트의 사용자 터미널에서 실행한다. 입력은 화면에 표시되지 않는다. 키는 명령 인자나 채팅에 넣지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts\setup_api.py
docker compose up -d --build app
```

도구는 `.local/gemini_api.key`와 `.env`의 `DA_LLM_PROVIDER=gemini`를 설정한다. 기본 모델은 `gemini-3.5-flash-lite`이며 `.env`의 `GEMINI_MODEL`로 바꾸고 컨테이너를 재생성할 수 있다. 기존 키 설정을 변경하는 도구이므로 교체할 키를 입력할 때만 실행한다. 초기 로컬 구성은 `scripts/setup_local.py`로 만든다.

키 파일은 Git과 Docker 이미지에서 제외한다. Compose secret으로 앱 서버만 읽는다. 파일을 교체한 뒤 앱 컨테이너를 재생성해야 새 키 파일이 반영된다. 호스트 파일은 로컬 평문 비밀이며 OS 관리자 접근을 막는 장치는 아니다.

## 실제 확인

[앱](http://127.0.0.1:8087)을 새로고침한다. 상단에 Gemini API가 표시돼야 한다. 키 설정은 실제 호출 검증과 구분한다. 코칭 1회 → 보고서 리뷰 1회 → 5개 평가 기준·근거·점수 표시 → 새로고침 후 리뷰 보존을 확인한다. 실제 키 없는 모의 응답 검증은 실제 모델 품질 검증을 대신하지 않는다.

## 비용·데이터·한도

Gemini 3.5 Flash-Lite는 공식 가격표에 무료 티어가 있지만 프로젝트별 요청·토큰·일일 한도가 적용된다. 결제가 연결된 프로젝트는 유료 티어일 수 있다. 앱은 Google Cloud 결제를 설정하거나 유료 모델로 전환하지 않으며 무료 요금을 보장하지 않는다. 한도 오류는 그대로 실패 처리한다. 프로토타입은 가상 데이터만 사용한다. 무료 티어 입력·출력은 Google 제품 개선에 사용될 수 있으므로 실제 개인정보·기밀자료는 넣지 않는다.

일반 코칭은 공개 문제·데이터 사전·초안·사용자가 선택한 근거만 전송한다. 보고서 리뷰에는 제출본·근거·평가용 정답 집계가 추가되며 비공개 기준 SQL·seed는 전송하지 않는다. 요청에는 store=false를 사용하지만 이 설정이 무료 티어의 제품 개선 이용 조건을 바꾸는 것은 아니다.

Gemini generateContent의 systemInstruction/contents로 지시와 사용자 자료를 구분한다. 후보 finishReason=STOP이고 일반 텍스트가 있을 때만 완료로 처리한다. 생각 내용, 부분 응답·안전 제한·잘못된 형식은 성공 리뷰로 저장하지 않는다. 현재 모델은 별도의 thinkingLevel을 지정하지 않으며 출력 최대4096토큰, 서버 HTTP timeout90초, 자동 재시도 없음.

## 공식 자료

- [가격표](https://ai.google.dev/gemini-api/docs/pricing)
- [프로젝트 사용 한도](https://ai.google.dev/gemini-api/docs/rate-limits)
- [generateContent API](https://ai.google.dev/api/generate-content)
- [모델 종료 일정](https://ai.google.dev/gemini-api/docs/deprecations)

실제 키 확인 후 모델 목록 조회200을 확인했지만 gemini-2.5-flash의 생성 요청은 신규 사용자에게 제공하지 않는다는404 응답을 받았다. Google 응답과 [현재 모델 안내](https://ai.google.dev/gemini-api/docs/generate-content/latest-model)에 따라 기본 모델을 gemini-3.8-flash로 변경했다. 무료 티어가 가격표에 명시되어 있으나 현재 프로젝트의 결제 상태는 별도 확인 대상이다.

실제 확인 결과: Gemini3.8 Flash 코칭·보고서 리뷰 완료 및 화면 표시·새로고침 후 보존을 검증했다. 모델의 평가 품질·반복 일관성 전체 검증은 아직 완료하지 않았다. 상세 내용은 [검증 기록](../tests/reports/verification-2026-10-03.md)을 따른다.

2026-10-03 후속: 사용자 요청으로 현재 모델과 신규 설정의 기본값을 gemini-3.5-flash-lite로 변경했다. 위3.8 Flash 완료 검증은 이전 모델의 검증 이력이다.
