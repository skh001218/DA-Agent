# Discord 후속 작업: 기존 DB 회귀 검증

- 검증일: 2026-10-06 (Asia/Seoul)
- 기준 커밋: 158c9afa675c0a790ee2f437b12a9d9cd9bf3211
- 별도 작업 위치: C:/Users/Administrator/.codex/worktrees/discord-db-verification/DA-Agent
- 결과: **375 passed, 19 skipped, 2 warnings** (37.91초)
- 증거: `discord-db-verification-2026-10-06.xml`

## 격리 및 실행

표준 docs/scripts/specs/examples/src/template/tests 구조가 모두 있어 이동 없이 진행했다. 기존 운영 웹/DB 컨테이너, 운영 비밀 파일, 웹 소스는 접근하거나 수정하지 않았다. 기존 da-agent-app 이미지를 새 테스트 runner에만 재사용했다.

테스트 네트워크는 discord-db-verification-net, PostgreSQL17.4 컨테이너는 discord-db-verification-db, runner는 discord-db-verification-runner다. 운영 포트를 열거나 기존 Docker 볼륨을 연결하지 않았다. 전용 초기화 스크립트로 learner/recorder를 만들고 테스트 전용 generator를 준비했다. training-001/v1,v2는 별도 워크트리의 무시되는 packages 폴더에 각각 200명으로 생성한 뒤 PostgreSQL에서 검증했다. 테스트 기록은 verify_request_로 시작하는 새 스키마에서만 생성하고 기존 verification 스크립트의 finally에서 삭제했다.

runner 환경은 RECORDS_DSN/LEARNER_DSN/ADMIN_DSN을 신규 컨테이너로 지정하고 PACKAGES_ROOT=/app/packages, GENERATOR_PASSWORD_FILE=/app/.local/generator.key, GENERATOR_DB_HOST=discord-db-verification-db, QUALITY_CALL_INTERVAL_SECONDS=0으로 구성했다. 실제 API 자격증명은 주입하지 않았다. Discord SDK는 runner에만 requirements-discord.txt로 설치했다.

실행 명령: `python scripts/verification/verify_request_training.py tests/automated`. 최종 실행에는 `PYTEST_ADDOPTS=-rs --junitxml=tests/reports/discord-db-verification-2026-10-06.xml`을 지정했다.

## 확인 결과

기존 보고서의 웹/DB 환경 미설정으로 건너뛴 44개 회귀 테스트를 모두 활성화해 통과했다. 실제 PostgreSQL에서 SQL 실행·차단·시간초과·결과 상한·선택 저장·다시 열기·보고 버전·평가 실패 저장, 품질 실험 저장/내보내기·멱등성·중단 회복, 이벤트/상태/평가 revision, 생성 계획 고정·수정·실제 테이블·승인/재시도·스키마 삭제를 확인했다. 모델 응답은 해당 기존 테스트의 통제 응답을 사용하므로 실제 Gemma 품질 검증으로 계산하지 않는다.

최종 남은 19개 skip은 이 워크트리에 Discord 전용 테스트 DSN을 주입하지 않은 교육1/흐름4/조회6/서비스8 테스트다. 기존 웹/DB 테스트는 skip 없이 실행했고 Discord 전용 통합은 별도 담당 검증에 맡겼다. SDK 명령 등록·권한 통제 테스트를 포함한 transport21개도 통과했다.

초기 실행은 신규 Docker host명을 GENERATOR_DB_HOST에 지정하지 않아 기본 db DNS를 찾는 9개 테스트가 실패했다(363 passed,22 skipped). 신규 host명 설정 후 전체가 통과했으므로 소스 수정은 필요하지 않았다. 경고2개는 기존 FastAPI TestClient/httpx 폐기 예정 경고다.

이 보고서는 실제 Discord 서버 접속, 실제 모델 응답 품질, 교육 효과/사람 검토 또는 운영 화면 변경 검증을 주장하지 않는다.
