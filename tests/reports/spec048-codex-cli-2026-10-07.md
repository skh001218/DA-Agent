# Spec048 Codex CLI 검증 — 2026-10-07

## 구현·로컬 검증

- CLI: 공식 Codex CLI 0.160.0, ChatGPT 로그인. API 키·비공개 backend HTTP 호출 없이 실제 JSON probe 성공. low 추론 노력·도구 비활성화 설정으로 확인.
- 전체 자동 검사: 684 passed, 80 skipped. 환경 조건이 있는 DB·외부 검사는 건너뛴 결과와 구분한다.
- CLI·배포 경계 검사: 51 passed. API 키/로그인 미완료 차단, 환경 격리, stdin, 정상 완료 이벤트, 잘못된 JSON, 시간 초과·프로세스 종료, 출력 제한, 예약 호출 실패, preflight 실패 시 기존 운영 미교체를 확인.
- 별도 임시 PostgreSQL에서 Discord 전용 회귀: 58 passed. 운영 DB·웹 DB를 사용하지 않았다.
- 개발 검증 이미지의 CLI 버전과 release 지문 검사 통과. 로컬 미커밋 이미지는 운영에 사용하지 않는다.
- 임시 DB의 실제 Codex 흐름: 플랫폼별 일일 활동량 과제 출제 2회(설계·독립 검토), SQL 조회 성공·실제 결과 저장, 선택 편향 용어 설명 성공, 평가 quality_validation=passed, 같은 과제·평가 재개 확인.
- 신규 공급자의 평가 품질 프로필은 pending이다. 피드백·검산 결과는 보존하고 total=null·held=true를 유지했다. 반복 품질 검증·사람 승인은 이번 smoke 검사로 완료 처리하지 않는다.
- 실패 흐름: 최초 튜토리얼 완료율 설계의 관측 기간 검산이 부족하다는 독립 검토 후 수정안이 plan_invalid로 거절됐다. 실패 요청과 진단은 보존됐으며 대체 문제를 그 요청의 성공으로 저장하지 않았다.
- CLI strict 스키마의 모든 필드 required 규칙과 기존 Recipe 계약이 맞지 않음을 실제 확인했다. 호환되는 응답 스키마에만 output-schema를 적용하고, Recipe 원문 스키마·서버 검증은 유지했다.

재현 도구: scripts/verification/verify_codex_cli.py, scripts/verification/verify_codex_discord_flow.py. 실제 호출에는 --live가 필요하며 구독 사용량을 소비한다. 원문 로컬 증거는 Git에 올리지 않는 .local/spec048에 보관한다.

## Git 반영

검증된 구현을 codex/discord-codex-cli에서 PR로 반영할 예정이다. PR·main CI 결과는 후속 확인 후 기록한다.

## 운영 반영

현재 운영은 기존 main b1505888b649e9f3457e04e17d1e6175e4c709f5·Gemma 공급자다. 새 main의 CI 성공 후 지정된 배포 명령으로 CLI를 명시적으로 선택한다. CLI 인증 preflight에 실패하면 기존 봇을 교체하지 않는다.

## 실제 Discord 화면 확인

아직 확인 전이다. 새 운영에서 /training → query/question → 보고·제출 → /resume의 핵심 흐름을 확인한다. 구현 테스트·모델 호출 성공을 운영 화면 확인으로 간주하지 않는다.
