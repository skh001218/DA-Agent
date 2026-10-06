# Discord PRD 실제 화면 후속 검증

- 날짜: 2026-10-06 (Asia/Seoul), 화면 테스트 약 16:09~16:13
- 결과: 연결과 시작, 과제/스레드 연결, 결과/SQL 표시 3개 추가 완료. PRD 8/12개 완료(66.7%).
- 확인 방식: computer-use 스킬과 cua_repl로 로그인된 Chrome Discord에서 실제 슬래시 명령을 실행하고 DOM·화면 결과 확인.

## 소스와 실행 환경 구분

현재 문서 편집 위치는 `C:/Users/Administrator/.codex/worktrees/629c/DA-Agent`, HEAD는 `df5058d6fefe37493c240717b03a30caa7f54184`다. 이전 검증에서 이 코드의 Discord 테스트 110개가 통과했다.

실행 중인 `da-agent-discord-bot-1`의 Compose 작업 위치는 `C:/Users/Administrator/.codex/worktrees/discord-mvp/DA-Agent`다. 이 폴더에는 미커밋 후속 구현(간결한 안내, 질문 답변, 이미지 표, 제출 결과, 명령 사용법, 응답 복구)이 있다. 이번에는 실행 소스를 읽기 전용으로 사용해 별도 DB 회귀를 수행했다. 후속 변경을 현재 체크아웃으로 복사·병합하거나 운영 봇을 재배포하지 않았다. PRD의 실제 제품 화면 완료 판단은 이 실행 환경을 대상으로 하며, 현재 체크아웃만 배포해 동일 화면이 나온다는 검증은 아니다.

회귀 실행 시 핵심 소스 SHA256:

| 파일 | SHA256 |
| --- | --- |
| discord_bot.py | A1DA819DC061629F5BD1BC2089387E5511EF9861A23E1483E29AAC5856BBFC0C |
| discord_transport.py | 51B3219D8E00C84D6BC068C346CCF1B1235F70639674FABB08D639D352A07EB0 |
| discord_service.py | 07139F56081EE9B9B9A00B5CFD92C9F852526E5ED3696816AF45B580D902E99C |
| discord_query.py | 43849366793E747D68F9AFECB2CC18E1D1DD608566DC746DB1F8851CE524A357 |

## 실제 화면 실행과 결과

서버 `1556888486919934064`, 부모 채널 `1556888698992468039`에서 기존 학습 과제를 수정하지 않고 새 검증 과제를 만들었다. 로그인된 한 사람 계정으로 실행했다. 두 사람 테스트로 계산하지 않는다.

| 실행 | 실제 확인 |
| --- | --- |
| 부모 채널 /training 기본 주제 | 새 비공개 스레드 DA-bce43019-8a0, 스레드 ID 1556926481580556318 생성. 소유자 추가 메시지와 업무 목표·가입 주간 09-08~09-14/09-15~09-21·UTC·관측 종료·데이터 한계·다음 행동 표시 |
| /query: 09-08 이상 09-22 미만 가입자, UTC, 채널별 고유 사용자 | 실행 a182ad60-5344-4972-ad8a-1e3553bfad6a. ads 19명, organic 21명. 계산 기준·tutorial-followup-v1·읽기 전용·5초 제한과 결과 표 표시 |
| /sql execution_id:a182ad60-5344-4972-ad8a-1e3553bfad6a | 동일 실행의 SELECT, COUNT DISTINCT, 가입일 경계, GROUP BY channel 표시 |
| /end → 같은 스레드 /resume | 모든 근거 저장 안내 후 같은 스레드에 과제 안내·상태 analysis·저장 조회 1·보고 0 표시. 미응답 멘토 질문도 다시 표시 |
| /query: 09-29 이상 09-30 미만 가입자, UTC, 채널별 | 실행 f43489c2-befe-482e-93dd-8d564668bf4d. 0행 이미지 표와 빈 결과 안내, 0%로 해석하지 않는다는 문구 표시 |
| 부모 채널 /query | 자신의 과제 스레드에서 명령을 사용하라는 거부 안내. 다른 과제·사용자의 기록을 열지 않음 |

과제 밖 명령 입력을 수정하는 과정에서 일반 메시지 `검증용 위치 오류 확인`도 부모 채널에 전송됐다. 봇의 거부 안내와 함께 DOM 증거에 그대로 남겼으며 성공한 조회로 계산하지 않았다.

검증용 과제와 생성된 메시지는 보존했다. 실제 조회 2회가 모델 호출을 사용했으며, 한도·비용이 0이라고 주장하지 않는다. 보고/평가를 새로 제출하거나 사람의 학습 성과로 계산하지 않았다. 기존 다른 과제에 있던 보고 버전 1~3, 후속 답변 저장, 평가/성장 자료 부족 메시지는 읽기 확인만 했으며 이번 실행 성공 건수에 포함하지 않는다.

### 화면 증거

- [조회 결과와 실행 SQL](../artifacts/discord-prd-live-sql-2026-10-06.jpg)
- [중단 후 재개](../artifacts/discord-prd-live-resume-2026-10-06.jpg)
- [빈 결과 안내](../artifacts/discord-prd-live-empty-2026-10-06.jpg)
- [과제 밖 접근 거부](../artifacts/discord-prd-live-access-error-2026-10-06.jpg)
- [과제 DOM 기록](../artifacts/discord-prd-live-flow-2026-10-06.txt), [부모 채널 DOM 기록](../artifacts/discord-prd-live-parent-2026-10-06.txt)

## 실행 봇 소스의 격리 회귀

전용 네트워크 `da-prd-liveaudit-20261006`, postgres:17.4 `da-prd-liveaudit-db-20261006`, 조회 DB `discord_test_liveaudit`, 기록 DB `discord_liveaudit_records`, 비관리자 learner를 새로 만들었다. 운영 포트·볼륨·DSN·토큰·API 키는 runner에 연결하지 않았다. 실행 봇 소스는 /app 읽기 전용 마운트, 증거 출력만 /audit-reports에 별도 쓰기 마운트했다. 기존 이미지의 의존성만 재사용하고 현재 실행 소스를 PYTHONPATH=/app/src로 지정했다.

```text
DISCORD_RESPONSE_DIRECTORY=/tmp/discord-responses
python -m pytest -q -p no:cacheprovider tests/automated/test_discord_transport.py tests/automated/test_discord_service.py tests/automated/test_discord_query.py tests/automated/test_discord_education.py tests/automated/test_discord_provider.py tests/automated/test_discord_gemma_conditions.py tests/automated/test_discord_flow.py tests/automated/test_discord_answers.py tests/automated/test_discord_responses.py --junitxml=/audit-reports/discord-prd-liveaudit-2026-10-06.xml
```

최종 **129 passed, 0 skipped, 0 failures/errors (3.94초)**. [최종 JUnit](discord-prd-liveaudit-2026-10-06.xml).

처음에는 응답 기록 기본 디렉터리가 읽기 전용 /app 아래여서 SDK 생성 테스트 10개가 실패하고 119개가 통과했다. 테스트 실행 설정에 응답 디렉터리를 쓰기 가능한 /tmp로 지정한 뒤 전체를 재실행했다. 소스나 안전 검사를 변경하지 않았다. [초기 실패 JUnit](discord-prd-liveaudit-initial-2026-10-06.xml)도 보존한다. 이전 110개와 공통 테스트를 합산하지 않는다.

빈 결과/NULL/10행 표시/불완전 수집 안내·SQL 열람 기록·소유권·서버 격리·삭제/403 모의 복구·실제 DB 저장/복원·질문 답변·응답 실패/만료/중단 복구가 통과했다. 실제 화면에서 보지 못한 10행 초과·불완전 수집·실제 삭제/403을 실제 서버 테스트로 주장하지 않는다.

## 문서 판정과 남은 작업

PRD의 기존 완료 조건과 12개 분모를 유지했다. 연결/시작은 실제 생성과 안내, 스레드 연결은 동일 과제 재개·저장 조회 보존·과제 밖 접근 실패 안내와 권한/복구 회귀, 결과/SQL 표시는 실제 결과·SQL·빈 결과 화면 및 표시 제한 회귀를 근거로 완료 처리한다. Spec025/026도 대응 화면 검증을 완료로 갱신한다.

다음 4개는 검증 대기를 유지한다:

1. 역할 선택과 대화 진행: 수준별 개입·도움의 교육 타당성 사람 검토와 도움 흐름 확인.
2. 보고와 후속 질문: 대표 과제의 공개 교육 기준과 보고 표본 사람 검토, 실제 수정 흐름 보완.
3. 최종 평가와 추천: human_review/comparability_review 승인, 비교 가능한 도움 전 기준/후속 관측. 실제 새 과제 생성이 성장 비교 승인은 아니다.
4. 실제 Discord 핵심 흐름: 두 사람 계정의 소유권/접근, 실제 삭제·보관·403, 교육/성장 전체 흐름. 실제 403 대신 과제 밖 위치 거부와 모의 테스트만 수행했다.

Spec027의 특정 기존 과제 복구 화면은 이번 새 과제 재개와 다르므로 완료로 바꾸지 않았다. Spec021~024의 서버/토큰 미설정·실모델 호출 없음 등 오래된 대기 사유는 현재 확인 범위로 갱신하되 해당 교육/전체 흐름 작업 상태는 유지했다.

현재 테스트에서 수정할 제품 코드 결함은 재현되지 않았다. 테스트 환경 설정 오류만 바로잡고 PRD·관련 Spec·검증 기록을 수정했다. 다음 실제 확인 행동은 별도 일반 사용자 B가 이 비공개 과제에 접근하거나 조작할 수 없는지 확인하는 것이다. 이 계정을 이번에는 사용할 수 없어 수행하지 않았다.

검증 종료 후 새 runner·DB 컨테이너·익명 볼륨·네트워크를 정리했다. PRD 8/12개·66.7%, Spec025/026 3/3개·100%, 문서 내부 링크, 최종 JUnit 129개 성공, git diff --check를 확인했다. 실제 SQL 화면 증거도 이미지를 열어 조회 표와 SQL이 함께 보이는지 확인했다.

## 후속 사용자 범위 결정 (2026-10-06)

사용자는 현재 본인만 사용할 것이므로 실제 Discord 핵심 흐름을 완료로 두기로 했다. 기존 테스트 결과는 그대로 보존하며 PRD 0.6의 해당 항목을 단일 계정 시작·조회·SQL·빈 결과·중단/재개·과제 밖 거부 기준으로 완료 처리한다. PRD는 9/12개 완료(75%)이고 교육·보고·최종 평가/성장 3개는 검증 대기다. 두 사용자·실제 삭제/보관/403은 미실행 후속 항목으로 남긴다. 새 테스트를 실행하지 않았으며 사용자의 판단과 이유를 나의판단과근거.md에 기록했다.
