# 200명 로컬 프로토타입 실행

2026-10-03. 현재 Spec 001~004 중 문제 1의 첫 훈련 흐름을 구현했다. 문제 2·3은 별도 Spec과 콘텐츠가 필요하다.

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

인증 코드와 모의 HTTP 테스트는 구현했지만 실제 계정의 로그인·플랜 사용·AI 응답은 아직 확인하지 않았다. [인증 안내](./auth-setup.md)를 따라 화면의 ChatGPT 연결을 누르고 직접 공식 로그인·승인을 완료한다. 연결 후 코칭 또는 리뷰를 요청해 완료 응답을 확인한다. 로그인 성공과 실제 AI 호출 성공은 별도로 표시한다. AI 실패 시에도 SQL·선택 저장·보고서·고정 힌트는 계속 사용할 수 있다.

다른 PC는 같은 Git 커밋, 고정 의존성, 생성된 v2 manifest 해시로 위 절차를 재현하고 새 PC에서 별도 로그인한다. 지금은 Windows/AMD64 호스트의 Linux/AMD64 Docker만 검증했다. ARM 및 실제 다른 PC 실행은 검증 대기다. 인증 키·개인 기록·DB 백업은 Git에 넣지 않는다.


## 현재 LLM 선택: Gemini API

2026-10-03 사용자가 비용 우려로 Gemini API를 선택했다. [키 설정·실제 확인 안내](gemini-setup.md)를 따른다. 기존 ChatGPT 로그인 접근 거부는 별도 미해결 기록이며 Gemini 테스트 성공으로 해당 OAuth 검증을 완료 처리하지 않는다.
