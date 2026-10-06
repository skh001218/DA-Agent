# PR 15–18 통합·병합 검증

- 검증일: 2026-10-06 (Asia/Seoul)
- 통합 대상: main의 Discord 구현·Spec·웹 Gemma/로그인과 PR18의 사례 기반 과제 생성.
- main 기준: 2fc62c1773508eca0e423cc1f5c4ab8d2c8c93c5.
- 충돌 파일: `.env.example`, `compose.yaml`, `나의판단과근거.md`.
- API 키 환경 설정과 검색 모델 설정을 모두 보존했고, 사용자 판단 기록은 두 내용을 구분선으로 나누어 모두 보존했다. 공급자의 키 우선순위·검색·Gemma 요청 기능과 테스트는 자동 병합 후 회귀 검사로 확인했다.

## 결과

- 임시 PostgreSQL 17.4를 별도 Docker 네트워크에서 실행. 운영 포트·운영 볼륨·실제 API 키를 사용하지 않음.
- training/records와 Discord 전용 데이터·기록 DB, 비관리자 학습 역할을 준비. 합성 training-001 v1/v2를 생성·검증.
- `scripts/verification/verify_request_training.py tests/automated`: **436 passed, 0 skipped**, 30.18초. [JUnit 결과](pr-merge-regression-2026-10-06.xml).
- `node --check`로 app.js와 training.js 검사 통과.
- `docker compose --env-file .env.example -f compose.yaml config --quiet` 통과.
- 충돌 표시와 `git diff --check` 검사 통과.
- 경고 2건: 기존 Starlette TestClient/httpx 및 httpx 요청 본문 인코딩 폐기 예정 경고.

## 검증 범위

이 결과는 병합한 코드의 자동·DB 회귀다. 실제 검색 할당량, 큰 Gemma 설계 요청 성공, 사람 계정 OAuth 발급, Discord 전체 화면 흐름, 교육 품질 승인을 대신하지 않는다. 앞선 기능별 Spec의 해당 미확인 상태를 유지한다. 실행 중인 웹·봇 컨테이너는 재배포하지 않았다.
