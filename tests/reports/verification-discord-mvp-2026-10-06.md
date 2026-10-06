# Discord MVP 통합 검증

- 날짜: 2026-10-06 (Asia/Seoul)
- 기준 커밋: 3079e7c
- 통합 브랜치: codex/discord-mvp
- 작업 위치: C:/Users/Administrator/.codex/worktrees/discord-mvp/DA-Agent
- 결과: 코드 구현·격리 DB 검증 완료, 실제 Discord/Gemini·사람의 교육 검토 대기

## 작업 분리

루트에서 docs/scripts/specs/examples/src/template/tests 표준 구조를 확인했고 모두 존재하여 파일 이동 없이 진행했다. 세 서브 에이전트가 discord-query, discord-education, discord-transport 별도 관리 워크트리에서 구현했다. 부모 discord-mvp 워크트리에 커밋을 통합하고 저장·진행·연결을 검증했다. 기존 8cb2 체크아웃에는 변경을 적용하지 않는다.

새 전용 모듈은 discord_store/service/query/education/transport/bot이며 기존 app.py, static, config.py, requirements/lock, compose.yaml, 운영 DB·컨테이너에는 변경하지 않았다. 의존성은 requirements-discord.txt로 분리한다. Spec019~023에 기능별 완료 조건·검증 결과·대기 항목을 기록했다.

## 최종 자동 결과

**350 passed, 44 skipped, 1 warning**. 경고는 기존 FastAPI TestClient의 httpx 사용 폐기 예정 안내다. 건너뛴 44개는 별도의 기존 웹 DB/패키지/환경을 요구하는 테스트이며 이번 테스트에서는 운영 DB에 연결하지 않았다. Discord 신규 88개는 모두 실행했다.

| 범위 | 통과 | 실제 확인 |
| --- | ---: | --- |
| 자연어 조건·SQL 엔진 | 36 | 대체 API의 정의 확인/공개 투영/동일 의도 재시도, 실제 PostgreSQL6개 표본 |
| 교육 계약·대표 데이터·평가·성장 | 18 | 실제 별도 learner SELECT·DELETE 차단, 대체 의미 평가 계약·보류·자료 부족 |
| Discord 전송·SDK | 21 | discord.py2.7.1 명령10개 등록, 모의 defer/권한/소유권/복구/동시 시작 |
| 영구 기록·서비스·통합 흐름 | 13 | 실제 별도 PostgreSQL, 두 사용자·서버, 동시 보고/한도, 전체 수집·보고·평가·재시작 |
| 기존 회귀 | 262 | 기존 코드 단위/가용 환경 테스트 |

격리 Docker 네트워크 da-discord-test와 별도 postgres:17.4 컨테이너 da-discord-test-db를 사용했다. 기록 DB는 discord_records_test, 데이터 DB는 discord_test이고 learner는 관리자와 다른 계정이다. 기존 da-agent-db-1은 사용하지 않았다. 기존 da-agent-app 이미지를 **새 일회용 컨테이너**에 사용하고 워크트리를 /app에 마운트했으며, 해당 일회용 컨테이너에만 Discord 의존성을 설치했다. 실행 중인 웹 컨테이너/이미지에는 설치하지 않았다.

검증 명령은 일회용 컨테이너에서 `pip install -r requirements-discord.txt` 후 `python -m pytest -q`다. 환경 변수는 PYTHONPATH=/app/src와 DISCORD_TEST_DSN, DISCORD_TEST_ADMIN_DSN, DISCORD_TEST_LEARNER_DSN, DISCORD_TEST_RECORDS_DSN을 **별도 테스트 서버**로 설정했다. 실제 Gemini는 호출하지 않았다.

## 핵심 흐름과 수정 검증

- 모호한 조회는 SQL 전에 확인 질문으로 대기하고, 중급의 중립 확인과 초급 선택지 도움을 구분했다.
- 실제 SqlRunner 미리보기1행 조건에서 전체2행을 즉시 회수해 영구 보존했다. 실행 SQL·조건·데이터 버전·원문·확인 답변을 연결했다.
- 결과 회수 실패는 실행이 성공했어도 성공 근거로 저장하지 않는다. 빈 결과/NULL/표시 제한/수집 불완전을 구분했다.
- SQL 열람·도움·가설·품질 판단을 기록하고 선택 근거 → 보고 버전 → 후속 답변 → 공개 기준 평가로 연결했다.
- 평가 실패는 보류, 총점 null로 처리하고 보고·실행을 보존했다. 도움 이후 최종 등급을 무보조 등급으로 계산하지 않았다.
- 같은 이벤트의 재전송은 모델·조회·평가를 중복 실행하지 않는다. 처리 중 중단된 이벤트는 자동 재호출하지 않는다.
- 동시에 같은 과제의 보고를 작성해 버전이 섞이지 않았다. 사용자별 일별 한도 예약도 원자적이며 한도 후 기록 열람이 가능했다.
- 최초 시작 응답의 오래된 thread_id를 재사용하지 않고 최신 연결을 읽는다. 같은 봇 프로세스에서 동시 시작의 스레드 생성을 잠금으로 묶어 하나만 생성했다.
- 읽기 전용 역할로 실제 지표 집계·중복·코호트·D7 관측 완료·주간 재방문·날짜 경계·분모0을 고정 결과와 대조했다.
- 긴 보고 append는 새 버전으로 합치고 이전 버전은 보존한다. 도움 수준과 난이도를 별도로 저장·공개한다.

검증 확대 중 조회 테스트가 특정 learner 역할명에 고정돼 실패했다. 테스트 DSN의 역할명으로 GRANT 대상을 읽도록 수정하고 최종 전체 테스트를 다시 통과했다.

## 기존 웹 비영향 확인

검증 전후 `http://127.0.0.1:8087/api/health`에서 status=ok, contract_version=v1을 확인했다. 운영 컨테이너 ID는 81bc29c72cb30881d1f178e3ecc57d0e53bc02b268d49be9b516d6c4930b9add, 시작 시간은 2026-10-06T02:34:28.946631996Z로 동일했고 healthy였다. 웹 재시작·배포·운영 데이터 변경을 하지 않았다. 웹 소스에 변화가 없으므로 새 웹 화면 검증을 Discord 화면 검증으로 계산하지 않았다.

## 남은 필수 검증

1. 실제 Discord 서버의 두 사용자 핵심 흐름, 비공개 권한/특권 운영자 접근, 재시작·스레드 보관/삭제/권한 오류, Intent 대안을 확인한다.
2. Gemini 사용이 가능해지면 실제 자연어 조건 해석, 공개 기준 평가, 실패·제한·응답 품질을 확인한다. 대체 API 통과는 실모델 품질 보장이 아니다.
3. 대표 과제·다른 타당한 결론·가설 수정·인과 단정·품질 판단·문체 불변 표본을 사람이 검토한다.
4. 후속 변형의 비교 가능성을 사람이 승인하고 실제 도움 전 관측을 확보한다. 지금은 성장 판단 자료 부족이며 교육 효과를 주장하지 않는다.
5. 실제 파일럿 서버·예산·기록 보관/삭제·운영 책임을 PRD13절에 따라 확정한다.

첫 화면 확인 행동: docs/discord-setup.md의 별도 환경을 설정한 뒤 사용자 A가 부모 채널에서 `/training topic:tutorial difficulty:intermediate`를 실행하고 비공개 스레드에 업무 목표·데이터 사전·공개 평가 조건이 표시되는지 확인한다. 실제 Discord 화면을 열 수 있는 서버/토큰이 없어 이번에는 확인하지 못했다. 출시 완료로 처리하지 않는다.
