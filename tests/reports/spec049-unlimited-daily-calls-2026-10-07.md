# Discord 하루 호출 제한 해제 검증

- 날짜: 2026-10-07 (Asia/Seoul)
- 요청: 봇 자체의 하루 모델 호출 제한 해제 및 운영 적용.
- 프로젝트 표준 폴더는 모두 존재했고 파일 이동은 필요하지 않았다.

## 구현과 자동 검증

- DISCORD_DAILY_CALL_LIMIT=0은 무제한이다. 기본값 30·양수 한도·음수 거절을 유지한다.
- 무제한에서도 UTC+9 날짜별 호출 횟수를 PostgreSQL에서 원자적으로 누적한다. 한도 재적용 시 기존 사용량을 기준으로 제한한다.
- --daily-call-limit은 해당 운영 설정만 변경한다. 생략하면 현재 값을 유지한다. --status에 실제 한도를 표시하며 설정 불일치 시 이전 구성으로 복구한다.
- 전체 자동 검사 697 passed·83 skipped. 이후 추가한 배포 검증까지 배포 검사 29 passed. 마지막 출제·CLI·배포 회귀 85 passed.
- 일회용 PostgreSQL의 Discord 회귀 61 passed. 30회 소진 후 12개 동시 무제한 예약의 결과 31~42, 양수 제한 재적용, 취소 트랜잭션 롤백을 확인했다. 해당 테스트 DB 컨테이너는 검증 후 제거했다. 운영 DB를 테스트용으로 사용하지 않았다.
- 요청별 출제 호출 상한·제한된 수정·취소 확인·CLI 직렬화·공급자 구독 한도는 유지했다.

## Git 반영과 운영 배포

- [PR #40](https://github.com/skh001218/DA-Agent/pull/40)의 automated-tests·discord-db-tests·discord-image가 모두 성공한 뒤 main에 병합했다.
- main ba0504116e217da314d2f5b2c602909ab156b850의 동일 세 CI도 성공했다.
- `python scripts/deploy_discord.py --apply --commit ba0504116e217da314d2f5b2c602909ab156b850 --daily-call-limit 0` 실행 결과 verified.
- 운영 상태: codex_cli, daily_call_limit=0, refs/heads/main, 소스 82개 지문 검증·Discord ready.
- 기능 확인 당시 이미지: sha256:ffb427b80d2728fd3ef388fba5cd6d208315914ddb8b72bfd5d22b2cb22d2ea2.
- 검증 문서도 PR·main CI를 거쳐 반영하고 최종 main을 재배포한다. 최종 커밋·설정은 --status가 기준이다.

## 실제 Discord 재시도

대상은 [실패했던 스레드](https://discord.com/channels/1556888486919934064/1557248613069885500)이며 세션 a45033a3-b191-4195-9dbd-e0665164cd9e의 원래 요청 「게임 내 재화 변동에 대한 분석을 하고 싶어」를 그대로 사용했다.

1. 배포 전: generation.status=failed, error_code=usage_limit, planning_calls=0, 당일 사용량 30회.
2. 실제 Chrome에서 해당 스레드의 출제 재시도 버튼을 클릭했다.
3. 13:43:01 KST 설계 호출 시작: planning_calls=1, 당일 사용량 31회, error_code=null.
4. 설계 응답 completed 후 13:44:53 KST 독립 검토 호출이 시작되고 completed로 완료됐다.
5. 최종 generation.status=ready, error_code=null, planning_calls=2, 당일 사용량 32회.
6. 스레드 제목이 「중급 - 분석 · 활동 기간별 골드 거래 변동 점검」으로 변경됐고, 계정 200행·골드 거래 750행의 과제 본문과 accounts·gold_transactions 공개 자료 안내가 실제 게시됐다.

일일 제한 해제와 문제 생성·게시를 모두 확인했다. 기존 실패 이력과 사용량은 보존됐다. 해당 과제는 자동 검증을 통과한 연습용 합성 자료이며 사람 품질 승인은 별도다. 이번 변경에서는 분석 제출·평가를 추가로 실행하지 않았다.

화면 증거는 Git 제외 .local/spec049/retry-running.png와 retry-ready.png에 저장했다. 비밀·인증정보·로컬 배포 구성은 Git에 추가하지 않았다.
