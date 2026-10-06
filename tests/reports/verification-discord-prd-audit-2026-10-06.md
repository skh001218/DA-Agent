# prd-discord 구현 상태 재검증

- 검증일: 2026-10-06 (Asia/Seoul)
- 기준 코드: df5058d6fefe37493c240717b03a30caa7f54184
- 작업 위치: C:/Users/Administrator/.codex/worktrees/629c/DA-Agent
- 목적: PRD의 검증 대기 8개 항목을 현재 구현·후속 검증 근거와 대조하고 완료 조건 충족 여부 판단.
- 결과: 실패·중복·비용 처리 1개를 완료로 변경. 전체 5/12개 완료(41.7%), 7개 검증 대기.

## 이번에 실행한 검증

표준 docs/scripts/specs/examples/src/template/tests 구조가 모두 있어 생성·이동하지 않았다. 호스트 Python에는 pytest가 없어 기존 Discord 이미지의 런타임을 새 일회용 runner에서 사용했다. 현재 워크트리를 /app에 마운트하고 PYTHONPATH=/app/src로 현재 코드를 읽었다. 운영 컨테이너에 명령을 실행하거나 운영 DB·비밀 설정·실모델을 사용하지 않았다.

전용 네트워크 `da-prd-audit-20261006`, 새 postgres:17.4 컨테이너 `da-prd-audit-db-20261006`을 생성했다. 호스트 포트와 운영 볼륨을 연결하지 않았다. 적재/조회 DB `discord_test_prd_audit`, 기록 DB `discord_prd_records`, 별도 비관리자 learner `prd_learner`를 사용했다. 조회·적재·기록 테스트 DSN을 이 테스트 컨테이너에만 지정했다.

실행 명령:

```text
python -m pytest -q tests/automated/test_discord_transport.py tests/automated/test_discord_service.py tests/automated/test_discord_query.py tests/automated/test_discord_education.py tests/automated/test_discord_provider.py tests/automated/test_discord_gemma_conditions.py tests/automated/test_discord_flow.py --junitxml=tests/reports/discord-prd-audit-2026-10-06.xml
```

최종 **110 passed, 0 skipped, 0 errors (4.08초)**. [최종 JUnit](discord-prd-audit-2026-10-06.xml).

첫 실행은 테스트 DB 이름을 `discord_prd_audit`로 지정하여 query fixture의 `/discord_test` 안전 검사에서 6개가 차단됐고 104개가 통과했다. 새 전용 `discord_test_prd_audit` DB를 만든 뒤 위 전체 범위를 재실행했다. 테스트의 안전 검사는 수정하지 않았다. [초기 JUnit](discord-prd-audit-initial-2026-10-06.xml)을 보존한다. 테스트 수를 이전 보고서와 합산하지 않는다.

## 실제 조회 DB 연결 실패 추가 검증

별도 일회용 runner에서 기존 통합 테스트의 ScriptedProvider와 현재 DiscordStore/DiscordTrainingService/DiscordQueryEngine/SqlRunner를 직접 연결했다. 정상 전용 DB에 과제를 준비한 다음 **runner의 learner DSN만 runner 내부 127.0.0.1:1의 연결 불가능 주소로 변경**했다. 기록 DB는 정상으로 유지했다. 실제 psycopg 연결 거부를 유발했으며 오류 응답을 모의로 만들지 않았다.

다음 assertion을 모두 통과했다:

- SQL 실행 결과 error, connection 오류의 시도 2회로 제한.
- 성공 executions가 없고 과제 상태는 analysis 유지.
- 사용자 응답은 조회 실패, 실패 쿼리·시도 기록은 실제 기록 DB에 보존.
- 동일 event_id 재전송은 기존 응답 반환, 모델 호출 횟수 증가 없음.
- 새 DiscordStore/서비스 인스턴스의 resume에서 동일 실패 기록 복원.

실행 출력: `PASS: real connection refusal, two bounded attempts, durable failure, duplicate suppression, restart recovery`.

추가 검증은 일회성 Python stdin 실행이며 일반 pytest 110개에 포함하지 않는다. 생성 과제 스키마는 finally에서 제거했다. 이번 검증은 조회 DB 연결 실패를 확인했으며 기록 DB 자체의 전체 장애나 실제 Discord 네트워크 장애를 모두 검증했다는 의미는 아니다.

## 검증 대기 항목별 판단

| PRD 작업 | 판정 | 확인한 근거 | 남은 완료 조건 |
| --- | --- | --- | --- |
| Discord 연결과 훈련 시작 | 검증 대기 유지 | 현재 SDK·전송 회귀, Spec025/026의 Gateway·10개 명령·주제 동기화 | 사람 계정 /training으로 생성된 비공개 과제 안내 화면 |
| 과제와 스레드 연결 | 검증 대기 유지 | 현재 복구·403·참가자 혼입 차단 회귀, Spec027의 실제 참가자 조회·validate_thread·resume 응답 | 사람 계정 재개와 삭제/권한 실패 안내 화면 |
| 역할 선택과 대화 진행 | 검증 대기 유지 | 현재 역할별 응답·도움 구분·도움 이력·무관 메시지 무응답 회귀 | 실제 대화 화면, 수준별 개입의 교육 타당성. 도움·후속 질문은 고정 문구로 구현됨 |
| 결과와 실행 SQL 표시 | 검증 대기 유지 | 실제 SQL 결과 수집, 빈 결과/NULL/표시 제한·분할·SQL 열람 저장 회귀 | 실제 /query·/sql 화면에서 출력 확인 |
| 보고와 업무 담당자 후속 질문 | 검증 대기 유지 | 보고 버전·append·후속 답변·재시작 복원, 기존 실 Gemma 제출 표본 | 대표 과제·보고 교육 기준의 사람 검토, 실제 보고/수정 화면 |
| 최종 평가와 다음 추천 | 검증 대기 유지 | 공개 배점·근거 검사·실패 보류·도움/무보조 분리·추천·성장 자료 부족 회귀, 기존 실 Gemma 14개 표본 | human_review/comparability_review 승인, 실제 도움 전 기준/후속 관측, 평가 화면 |
| 실패·중복·비용 처리 | **완료로 변경** | 실제 DB 이벤트 원자성·중복/중단·일별 한도·실패 평가 보류·전체 결과 확보 실패 회귀, 위 실제 연결 거부·복원 | 이 항목의 상태/기록 완료 조건 충족. 실제 Discord 오류 화면은 마지막 작업에서 확인 |
| 실제 Discord 핵심 흐름 검증 | 검증 대기 유지 | 기존 runtime의 실제 연결·명령·참가자 확인 | 두 사람 핵심/오류 흐름·교육 표본 승인·도움 전 성장 관측 |

비용은 단가 미확정으로 null이며 0원으로 간주하지 않는다. 완료 변경은 PRD의 중복·API 실패·조회 DB 오류·한도 초과 시 상태/기록 일관성 조건에 따른다. 운영 예산 확정이나 모든 종류의 장애 검증 완료를 뜻하지 않는다.

## 후속 기록 반영과 한계

[Gemma 후속 검증](verification-discord-gemma-2026-10-06.md)의 실모델 14개 표본과 [독립 실행/스레드 복구 검증](verification-discord-runtime-2026-10-06.md)의 실제 Gateway·명령·참가자·SDK 재개 확인을 기존 근거로 읽어 반영했다. 이번에는 실제 모델 호출·서버 메시지 전송·사용자 화면 조작을 재실행하지 않았다. 실제 화면을 확인할 사람 계정/두 사용자 세션을 이 작업에서 사용하지 않아 해당 항목을 완료 처리하지 않았다.

사용자가 화면에서 먼저 확인할 행동: 지정 서버의 기존 과제 스레드에서 `/resume`을 실행하고 업무 목표·데이터 사전·공개 평가 조건이 다시 표시되는지 확인한다. 이 한 번의 확인만으로 두 사용자 전체 흐름이나 교육·성장 승인이 완료되지는 않는다.

PRD 0.4에서는 초기의 구현 후보·서버/모델 미검증 문구를 현재 구현 근거와 구분하고, 완료 조건·12개 분모를 유지했다. 코드 수정과 출시 완료 선언은 하지 않았다. 검증 종료 후 새 runner·DB 컨테이너·익명 볼륨·네트워크를 정리했다. PRD 상태 집계(12개 중 완료 5개), 문서 내부 파일 링크, 최종 JUnit 110개 모두 성공, git diff --check도 확인했다.
