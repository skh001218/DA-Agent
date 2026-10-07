# SQL 문제 템플릿 운영 반영 — 2026-10-07

## 반영 상태

사용자의 후속 운영 반영 요청에 따라 기능 브랜치·검증·PR·PR CI·main 병합·main CI·공식 배포·실제 Discord 확인을 진행했다.

| 구분 | 상태 | 근거 |
| --- | --- | --- |
| 구현·로컬 검증 | 완료 | 전체 869 passed / 89 환경 의존 skipped. 관련 회귀 92 passed / 18 DB 환경 의존 skipped. 로컬 브라우저·PNG 확인 |
| Git 반영 | 완료 | [PR #59](https://github.com/skh001218/DA-Agent/pull/59), PR head ed3a77b·main b40b60e의 automated-tests / discord-db-tests / discord-image 성공 |
| 운영 배포 | 완료 | `python scripts/deploy_discord.py --apply --commit b40b60e9ef7f389e2e27ac6a3c2014e2308acdba` 성공, Discord 연결·실행 파일 88개 인증 |
| 실제 Discord 확인 | 완료 | 새 중급 SQL 문제 생성, 구역 순서·이미지 2장·확대·답장 SQL 실행·재개 동일 형식 확인 |

운영 커밋은 **b40b60e9ef7f389e2e27ac6a3c2014e2308acdba**다. 이미지 ID는 `sha256:e04f6ae873682149d6e202020806e582bdb73a69e8ab4c0e8f01d58c097f7149`. 기존 Codex CLI 제공자와 일일 호출 한도 0(무제한)을 유지했다. 배포 전 커밋은 08494ba였다. 비밀 환경·배포 구성은 Git 제외 `.local`에 보존했다.

이 후속 문서·검증 근거 커밋은 실행 소스를 바꾸지 않는다. 후속 문서 main 커밋과 위에서 인증한 운영 코드 커밋을 구분한다.

## 실제 흐름

1. Discord의 da-agent 채널에서 `/training`의 SQL 연습을 선택하고 채널별 D7 리텐션, 이용자별 합성 관측 요약, 무접속 이용자 포함, 관측 일수 7 미만 제외를 입력했다. 중급 과제 `70ed7c37-c0fb-4cf7-9ec5-0d10ad9d549e`가 모델 설계·검토 2회 뒤 준비됐다.
2. 목표와 사용할 데이터 안내 뒤에 profiles·user_observations 데이터 사전 이미지 2장이 표시됐다. 그 뒤 계산 조건·출력 형식·제출 방법·SQL 입력 틀이 이어졌다. 실제 Discord 메시지의 순서와 첨부 개수를 읽어 확인했다.
3. profiles 데이터 사전 이미지를 실제 Discord 미디어 뷰어에서 확대하고 닫았다. 이미지를 로컬 HTML로 대체한 확인이 아니다.
4. 공개된 표·컬럼·조건으로 작성한 JOIN과 COUNT FILTER SQL을 제공된 코드 틀 메시지에 멘션 답장했다. 실행 `ee7bb89a-992d-4987-824f-874c3e0b445e` 성공, 전체 결과 2행이 저장되고 결과 이미지가 표시됐다. paid·organic 각각 분모 80, 분자 40, 비율 0.5였다. 이 수치는 연습용 합성 자료의 값이다. 이번 확인에서 최종 평가 `/submit`은 실행하지 않았다.
5. 같은 스레드에서 `/resume`을 실행했다. 첫 안내·재개 안내의 본문이 일치하고, 두 번 모두 데이터 이미지 2장 뒤에 조건 구역이 이어지는 것을 확인했다. 출제 호출은 2회, SQL 시도는 1개로 유지됐다. 스레드는 계속 풀이 가능한 상태로 남겼다.

운영 실제 흐름은 중급 생성형 문제 1개에서 확인했다. 생성형·고정 문제의 초급·중급·고급 형식과 첨부 실패 대체는 앞선 로컬 검증에서 확인했다. 모든 모델 출력의 문장 길이·교육 효과·사람 품질 승인을 이 확인으로 주장하지 않는다.

## 근거

- [실제 문제 스레드](https://discord.com/channels/1556888486919934064/1557322552400154686)
- [운영 상태](../artifacts/sql-problem-template-release-2026-10-07/runtime-status.json)
- [PR CI](../artifacts/sql-problem-template-release-2026-10-07/pr-ci.json), [main CI](../artifacts/sql-problem-template-release-2026-10-07/main-ci.json), [병합](../artifacts/sql-problem-template-release-2026-10-07/merge.json)
- [실제 전송·실행·재개 근거](../artifacts/sql-problem-template-release-2026-10-07/live-smoke.json)
- [문제 제목과 구역](../artifacts/sql-problem-template-release-2026-10-07/discord-template-header.png)
- [이미지 뒤 계산 조건](../artifacts/sql-problem-template-release-2026-10-07/discord-data-and-conditions.png)
- [실제 이미지 확대](../artifacts/sql-problem-template-release-2026-10-07/discord-dictionary-expanded.png)
- [SQL 실행 화면](../artifacts/sql-problem-template-release-2026-10-07/discord-sql-execution.png)
- [처음 표시된 메시지 DOM](../artifacts/sql-problem-template-release-2026-10-07/discord-first-messages.json)
