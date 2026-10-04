# 200명 로컬 프로토타입 실행

2026-10-04 갱신. 기존 200명 고정 계산 훈련과 PRD-v2 요청 기반 훈련을 실행한다. 고정 문제 2·3 계획은 PRD-v2로 대체했다. 생성 규칙과 실제 평가 품질의 사람 승인은 별도다.

## 실행

Docker Desktop을 실행한 뒤 프로젝트 루트에서 다음을 수행한다. Python 3.12가 필요하다. 포트 기본값은 8087이다.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock.txt
.venv/Scripts/python scripts/setup_local.py
.venv/Scripts/python scripts/generate_package.py --version v2 --users 200
.venv/Scripts/python scripts/verify_release.py
docker compose up -d db
docker compose --profile tools run --rm --build prepare
docker compose up -d --build app
```

`setup_local.py`는 `.env`, `.local/auth.key`, `.local/gemini_api.key`와 요청별 데이터 생성용 `.local/generator.key`를 없는 경우에만 만들며 기존 비밀값을 보존한다. Gemini 키 파일은 처음에는 비어 있으므로 [설정 안내](gemini-setup.md)에 따라 실제 키를 넣는다. 생성 비밀번호는 Compose의 `generator_password` secret으로 앱에 전달하며 화면이나 Git에 공개하지 않는다.

`prepare`는 `.local`과 `packages`를 연결하고 기존 DB에 생성 전용 `generator` 계정을 준비한 뒤 패키지 DB를 구성·검증한다. 기존 DB를 사용하는 업데이트에도 `setup_local.py` → `docker compose --profile tools run --rm --build prepare` → `docker compose up -d --build app` 순서를 사용한다. `prepare`는 생성 키와 DB 계정 비밀번호를 맞추며 사용자 기록을 초기화하지 않는다. 생성 패키지를 저장하기 위해 앱의 `packages` 볼륨은 쓰기 가능하다.

이미 같은 패키지 버전이 있으면 생성 명령은 덮어쓰지 않고 중단한다. 생성 단계를 건너뛰고 검증·시작 단계부터 진행한다. `prepare`도 기존 배포 DB가 원래 CSV와 같은지 확인하며, 학습 기록을 초기화하지 않는다.

[프로토타입 열기](http://127.0.0.1:8087). 포트를 변경하려면 `.env`의 WEB_PORT를 바꾼 뒤 `docker compose up -d app`을 실행한다. OAuth 콜백 주소도 Compose에서 해당 포트로 변경된다.

중지: `docker compose stop`. 재시작: `docker compose up -d`. named volume의 데이터와 인증은 유지된다. 일반 시작에 볼륨 삭제를 포함하지 않는다. 여러 서버 worker를 실행하지 않는다. 미저장 실행과 OAuth 진행 상태는 한 프로세스의 메모리에서 처리한다.

## 고정 조건과 API

- 공개 users: 200행. 가입 기간은 Asia/Seoul 2026-09-01 00:00 이상, 09-15 00:00 미만이다. 종료 경계 검증용 유저 1명은 가입 코호트 밖이다.
- 수집 완료 경계: 2026-09-19 00:00 Asia/Seoul 미만 데이터. UTC 교환과 PostgreSQL timestamptz 저장을 사용한다.
- 첫 패키지: training-001/v2. 이전 v1은 초기 생성한 스냅샷으로 기존 시도 재개 검증에 사용했고 로컬 packages 폴더에 유지한다. 새 PC는 현재 생성기로 v2를 생성하고 기록은 빈 상태로 시작한다. v1 기록을 옮기려면 원래 v1 패키지와 기록 DB 백업을 함께 옮긴다.
- 생성기 기본 seed는 비공개 manifest에 기록하며, 출제는 실제 PostgreSQL 정답 검증 후에만 허용한다. Git의 release-fingerprints.json과 생성된 manifest를 대조한다.
- 결과 미리보기 최대 200행. 선택 저장 최대 1,000행·직렬화 결과 1MiB. 제한에 걸리면 전체 행 수를 미상으로 표시하고 부분 결과임을 유지한다.
- 임시 실행은 10분 동안 메모리에만 보관한다. 최대 100개의 실행이며, 용량 제한·서버 재시작으로 더 일찍 만료될 수 있다. 저장할 때 서버의 실행 당시 원문·결과를 사용한다.
- SQL은 SELECT 한 문장과 공개 학습 표·허용 함수만 사용한다. 읽기 전용 계정, 별도 DB 권한과 5초 실행 제한을 함께 적용한다. 정책 밖의 분석 함수가 필요하면 허용 목록과 테스트를 검토해 확장한다.
- API 요청·응답 계약 v1은 `src/da_agent/contracts.py`와 `app.py`에 정의돼 있다. 초안 수정 번호 충돌은 409, 임시 실행 만료는 410으로 반환한다. 저장·제출·리뷰 요청에는 request_id를 사용한다.

## 확인 방법

```powershell
.venv/Scripts/python -m pytest -q
docker compose run --rm -e RUN_DB_TESTS=1 -v "${PWD}/tests:/app/tests" app python -m pytest -q
```

첫 명령은 DB 없는 테스트만 실행하며 통합 테스트를 건너뛴다. 두 번째는 실제 PostgreSQL 통합 테스트까지 실행한다. 테스트에서 새로 만든 훈련 기록은 테스트 종료 시 해당 ID만 제거한다.

화면에서는 새 훈련 시작 → SQL 입력·실행 → 필요한 실행만 기록에 저장 → 새로고침 → 보고서에 근거 연결 → 제출 → 수정 제출을 확인한다. SQL·미저장 결과는 새로고침 후 복구되지 않아야 한다. 서술 초안과 선택한 근거는 복구돼야 한다.

## 인증과 다른 PC에서 남은 확인

ChatGPT 인증 코드와 모의 HTTP 테스트는 구현했지만 실제 ChatGPT 계정의 로그인·플랜 사용·AI 응답은 아직 확인하지 않았다. 현재 선택한 Gemini 경로는 실제 모델·DB 연결을 확인했으며, 이 결과로 ChatGPT OAuth를 완료 처리하지 않는다. [인증 안내](./auth-setup.md)를 따라 화면의 ChatGPT 연결을 누르고 직접 공식 로그인·승인을 완료한다. 연결 후 코칭 또는 리뷰를 요청해 완료 응답을 확인한다. 로그인 성공과 실제 AI 호출 성공은 별도로 표시한다. AI 실패 시에도 SQL·선택 저장·보고서·고정 힌트는 계속 사용할 수 있다.

다른 PC는 같은 Git 커밋, 고정 의존성, 생성된 v2 manifest 해시로 위 절차를 재현하고 새 PC에서 별도 로그인한다. 지금은 Windows/AMD64 호스트의 Linux/AMD64 Docker만 검증했다. ARM 및 실제 다른 PC 실행은 검증 대기다. 인증 키·개인 기록·DB 백업은 Git에 넣지 않는다.


## 현재 LLM 선택: Gemini API

2026-10-03 사용자가 비용 우려로 Gemini API를 선택했다. [키 설정·실제 확인 안내](gemini-setup.md)를 따른다. 기존 ChatGPT 로그인 접근 거부는 별도 미해결 기록이며 Gemini 테스트 성공으로 해당 OAuth 검증을 완료 처리하지 않는다.


## 요청별 생성 규칙과 운영 검증

기본 훈련 요청에서는 기존 데이터를 사용할 수 있다. 새 설치의 네 가지 생성 규칙은 `draft`이며 생성 데이터 요청은 사람 승인 전 차단된다. 운영자 [품질 화면](http://127.0.0.1:8087/static/quality.html)의 ‘출제 규칙 검토’에서 규칙을 선택하고 ‘세 수준 표본 생성’을 누른다. 실제 PostgreSQL 검증을 통과한 초급·중급·고급 표본의 목표·요구 판단·사전·행 수·미리보기를 검토한다. 승인에 사용할 표본 3개 이상을 직접 선택하고 실제 검토 확인·검토자 입력 후 승인한다. 자동 생성·검증 성공은 사람 승인과 다르다. 코드가 승인을 대신하지 않는다.

‘v2 평가·코칭 반복 검증’에서는 고정 계획이 있는 v2 훈련과 검증 종류를 선택한다. 실행마다 6개 표본 × 3회인 18번의 실제 AI 호출과 비용이 발생한다. 회차 실패·모델·사용량·자동 판정·사람 판정·점수 범위를 확인하고 필요한 표본·회차를 사람 판정에 연결한다. 8종 지표는 현재 필터와 정의 버전을 포함한 JSON/CSV로 내보낼 수 있다.

2026-10-04 현재 실제 모델 출제·SQL·완료 리뷰·저장 재개·검증 전용 기록 삭제와 브라우저 핵심 흐름을 확인했다. [UI 검증 기록](../tests/verification-v2-ui-2026-10-04.md)과 [브라우저 결과](../tests/browser-v2/result.json)를 참고한다. 실제 반복 평가 18회 결과는 최종 미달·의미적 승인 없음이다. 불확실성 표본 점수 범위 25점, API 호출 제한 실패 2회 및 사람 판정 대기를 [반복 평가 결과](../tests/verification-v2-quality-live-2026-10-04.json)에 보존했다.

다른 PC·ARM 실행과 실제 5명 파일럿은 미수행이다. 현재 Windows/AMD64 환경의 실행·브라우저 확인만으로 다른 환경의 재현성과 학습 효과를 완료로 표시하지 않는다.
