# ChatGPT 인증 설정과 확인

2026-10-03 공식 OpenAI Docs에서 로컬 오픈소스 앱의 Sign in with ChatGPT 플랜 사용 경로를 확인했다. 초기 등록은 `dynamic_agent_client`를 사용하며 브라우저 콜백이 반환한 **issued client_id**로 토큰을 교환한다. API 키·client secret은 이 경로에 필요하지 않다. Codex 인증 파일과 ChatGPT 비공개 backend-api는 사용하지 않는다.

## 서버 설정

필요 라이브러리: `httpx`, `PyJWT[crypto]`, `cryptography`. 통합 프로토타입에서 확인·고정한 버전은 각각 0.28.1, 2.10.1, 46.0.5이며 requirements.lock.txt를 따른다.

| 환경 변수 | 기본값 / 사용 방법 |
| --- | --- |
| `DA_AUTH_STORAGE_PATH` | `runtime/auth/credentials.enc`; Git·기록 DB와 분리된 영속 볼륨에 둔다. |
| `DA_AUTH_ENCRYPTION_KEY_FILE` | 별도 보호된 Fernet 키 파일, Docker에서는 읽기 전용 secret으로 마운트한다. |
| `DA_AUTH_ENCRYPTION_KEY` | 키 파일 대신 사용할 수 있는 서버 환경 변수. 브라우저에 전달하지 않는다. |
| `DA_AUTH_REDIRECT_URI` | `http://127.0.0.1:8000/auth/callback`; 실제 호스트 앱 포트와 일치해야 한다. |
| `DA_AUTH_CLIENT_ID` | `dynamic_agent_client`; 이미 등록한 클라이언트 ID로 명시할 수도 있다. 정상 로그인 후 저장된 ID를 우선 사용한다. |

Fernet 키는 다음 명령으로 한 번 생성하고 서버 밖의 secret 관리 위치에 보관한다. 출력·파일을 Git, 로그, 대화에 붙여 넣지 않는다. Linux에서는 키 파일과 인증 볼륨에 소유자만 접근하도록 설정하고, Windows에서는 파일 보안 속성의 ACL을 사용 계정으로 제한한다. 암호화 키와 인증 파일을 같은 공개 볼륨에 넣지 않는다. 키를 잃으면 인증 저장소를 복구할 수 없다.

```shell
python scripts/setup_local.py
```

키가 없거나 인증 파일을 해독하지 못하면 `unavailable`을 표시하며, 기존 호스트 ID를 임의로 재생성하지 않는다. 파일은 Fernet으로 인증 암호화하고 원자적으로 교체하며 Linux 파일 권한은 `0600`이다. OS 관리자 또는 서버 프로세스에 접근 가능한 사람으로부터의 보호까지 제공하는 방식은 아니다.

이 모듈은 **서버 프로세스 1개**를 전제로 한다. pending state는 서버 메모리에 10분 동안 유지되고 한 번만 사용한다. 동일 프로세스의 토큰 갱신은 잠금으로 직렬화한다. 여러 worker를 실행하려면 pending transaction과 refresh 잠금을 프로세스 간 공유 저장소로 옮겨야 한다.

## API 연결 계약

- `AuthService.status()`는 `state`, `connected`, `plan_enabled`, `inference_verified`, 공개 계정 목록을 반환한다. 로그인 완료를 호출 성공으로 간주하지 않는다.
- `start(registration_id=None, new_account=False)`는 `authorization_url`을 반환한다. 연결 버튼을 `Continue with ChatGPT`로 표시하고 시스템 브라우저를 연다. 반환 URL에 ID token hint가 포함될 수 있으므로 URL 전체를 로그에 남기지 않는다.
- 콜백 라우트는 `/auth/callback`이며 `code`, `state`, `client_id`, `error`를 `callback()`에 전달한다. 콜백 URL에 code가 포함되므로 HTTP access log에서 쿼리 문자열을 제거하거나 해당 로깅을 끈다. 콜백 결과를 표시한 뒤 브라우저 URL을 앱 기본 주소로 교체한다.
- 서버가 먼저 콜백을 수신할 준비를 마친 뒤 로그인을 시작한다. 공식 문서는 `127.0.0.1`을 요구한다. `localhost`를 대신 쓰지 않는다. Docker는 앱 포트를 루프백으로 공개하되 컨테이너 서버는 내부에서 `0.0.0.0`으로 수신한다.
- `models()`는 현재 OAuth 토큰으로 공개 `/v1/models`를 조회하고 `visibility=list`인 `slug`, `display_name`을 반환한다. 모델을 로그인 전에 고정하지 않는다.
- `review(messages, model=None)`는 공개 `/v1/responses`에 `store=false`, `stream=true`로 호출한다. 모델 생략 시 현재 목록의 첫 모델을 선택한다. 사용자가 선택한 모델을 명시할 수 있다. `response.completed`를 받은 경우에만 `completed`와 결과 텍스트를 반환한다. 한도 실패·불완전·중단 시 부분 텍스트를 성공 결과로 반환하지 않는다.
- `disconnect()`는 발견된 공식 revocation endpoint로 refresh token 폐기를 시도한 뒤 로컬 토큰을 제거한다. 등록 정보와 호스트 ID를 유지한다. 원격 폐기 미확인 시 `remote_revocation_unconfirmed`를 표시하고 [ChatGPT 설정](https://chatgpt.com/settings/usage)에서 연결 해제할 수 있다.

계정마다 issued client ID와 검증된 subject를 함께 저장한다. `new_account=True`로 새 등록을 시작할 수 있으며 기존 등록을 덮어쓰지 않는다. 같은 계정의 재인증 중 새 identity 검증이 실패하면 기존 자격 증명을 교체하지 않는다. 인증 결과에는 토큰·PKCE verifier·nonce를 반환하지 않는다. AI 입력과 출력은 이 인증 저장소에 저장하지 않는다.

## 확인 결과와 남은 검증

오프라인 테스트는 실제 RSA 서명을 이용한 ID token 검증과 모의 HTTP 응답으로 PKCE·state·nonce, issuer·audience·expiry, 동적 등록 ID, 계정 불일치, 권한 부족, 모델 목록, `response.completed`, 스트림 중단·한도 초과, 토큰 갱신·폐기와 암호화 재시작을 확인한다.

실제 계정 로그인·사용 동의·컨테이너 콜백 도달·모델 조회·완료된 플랜 요청은 아직 검증하지 않았다. 이를 모두 확인하기 전 Spec 004의 인증·호출을 완료로 표시하지 않는다. 실제 확인 행동은 보호된 키를 설정하여 앱을 시작한 뒤 **Continue with ChatGPT → 브라우저 승인 → 모델 조회 → 짧은 리뷰 요청 → 연결 해제** 순서로 실행하는 것이다. 지원 플랜·워크스페이스·한도 정책에 따라 공식 서버가 거부할 수 있으며 SQL·저장·보고서 기능은 유지되어야 한다. 유료 API 키로 자동 전환하지 않는다.

## 공식 근거

- [OSS 등록·로그인](https://developers.openai.com/siwc/token-sharing-open-source/sign-in): 동적 등록, 콜백 client ID, PKCE, OIDC 검증, 보호된 저장.
- [계정·세션 관리](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions): 계정별 등록, 갱신, 원격 폐기, 로그에서 인증 URL 제거.
- [모델·추론](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference): 계정별 모델 목록, 공개 Responses API, 완료 이벤트.
- [OIDC 검증 예시](https://developers.openai.com/siwc/website): 공식 discovery·JWKS와 issuer·audience·nonce 검증. 이 앱은 파트너 웹사이트 flow가 아닌 OSS public-client flow를 사용한다.

## 2026-10-03 실제 로그인 차단

사용자가 공식 화면에서 “조직에서 이 앱에 접근 권한을 부여하지 않았습니다.”를 확인했다. 앱에는 계정 등록·권한 승인·실제 호출이 확인되지 않았다. 메시지는 접근 정책에 따른 거부를 시사하지만 조직 관리자 설정, 계정 지원 범위, 앱 등록 제한 중 어느 원인인지는 아직 확정하지 않았다. 조직 계정이면 관리자에게 해당 앱의 접근 허용 여부를 확인하고, 개인 계정이면 공식 지원에 계정·앱 등록 제한 확인을 요청한다. 인증 코드·토큰·전체 인증 URL은 공유하지 않는다.

[공식 오류 안내](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)는 선택한 사용자·워크스페이스·정책에 따른 거부 시 반복 OAuth를 피하고 제한을 표시하도록 안내한다. [Preview 요구사항](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)에 맞춰 지시 메시지는 developer 역할을 사용한다. 실제 승인 전에는 SQL·저장·보고서 흐름만 검증 완료 상태를 유지한다.
