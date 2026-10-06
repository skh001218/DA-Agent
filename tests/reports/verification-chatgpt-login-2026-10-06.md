# ChatGPT 로그인 버튼 연결 검증

- 일자: 2026-10-06 (Asia/Seoul)
- 실행 앱: http://127.0.0.1:8087, Docker Compose 재빌드 완료
- 범위: Gemini 훈련 공급자를 유지하면서 별도 ChatGPT 로그인 연결 확인

## 자동 검증

`python -m pytest tests/automated/test_chatgpt_login.py tests/automated/test_auth.py tests/automated/test_api_provider.py -q`: 41개 통과. 별도 시작·상태·해제 경로와 콜백 전달, Gemini 인스턴스 유지, 시작 실패 503, 다른 Origin 차단 확인. 기존 RSA 서명·PKCE/state/nonce·암호화 저장·토큰 갱신·폐기 테스트 포함. 실제 OpenAI 토큰 발급을 대신하는 테스트는 아니다. Starlette testclient의 기존 httpx 사용에 대한 폐기 예정 경고 1건.

`node --check src/da_agent/static/app.js`: 통과.

## 실제 브라우저

Codex 내장 브라우저에서 실제 실행 앱을 열어 확인했다.

- Gemini 연결 표시와 Continue with ChatGPT 버튼이 함께 표시됨.
- 요청 입력 후 로그인 버튼 클릭 시 입력 유지.
- 실제 서버가 발급한 공식 인증 URL로 새 창 이동, `https://auth.openai.com/log-in`의 이메일·Google·Apple·Microsoft 로그인 옵션 도달.
- 잘못된 state의 콜백을 실제 앱에 전달했을 때 로그인 실패 `invalid_authorization_state` 표시와 재시도 버튼 유지. 콜백 query는 기본 주소로 제거됨.
- 새로고침 후 Gemini 표시와 로그인 버튼 유지.
- 변경 후 재빌드하고 로그인 시작을 다시 확인함.

## 미수행

- 실제 사용자 계정 로그인과 권한 승인, 실제 OAuth 토큰 발급·저장 및 성공 상태 표시.
- 실제 ChatGPT 모델 호출. 이번 요청은 로그인 버튼 연결까지만 구현한다.
- 실제 Gemini 추론 재호출. Gemini 소스·설정과 훈련 경로는 변경하지 않았고 공급자 관련 회귀 테스트가 통과했다.

사용자는 로그인 버튼으로 공식 계정 로그인·권한 승인을 완료한 뒤 훈련 창으로 돌아와 로그인 및 플랜 권한 표시를 확인하면 된다.
