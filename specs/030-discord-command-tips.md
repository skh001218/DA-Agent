# Discord 명령어별 사용법 조회

## 구현 진행도

- 진행도: 2/2개 완료 (100%)
- 마지막 갱신일: 2026-10-06
- 남은 작업: 없음
- 차단 사유: 없음

| 작업 | 상태 | 완료 조건 | 관련 코드 / 검증 결과 |
| --- | --- | --- | --- |
| 명령 등록·사용법 | 완료 | 전체 명령의 옵션·예시, 슬래시 허용·목록·미지원 안내, 상태/모델 무호출 | discord_presentation.py, discord_transport.py, discord_bot.py |
| 실제 Discord | 완료 | 운영 명령 등록 및 /tip command:report 화면 확인 | tests/artifacts/discord-tip-report-2026-10-06.png; 부모 채널에서 본인 전용 설명 응답 확인 |

## 요구사항

- 후속 [Spec036](036-discord-text-task-generation.md) 구현 시 `/tip training`의 topic 선택 안내를 필수 text 요청·난이도·도움 수준 예시로 변경한다. 현재 도움말 구현과 새 형식의 검증 진행도는 구분한다.

Discord 슬래시 명령 /tip의 command 옵션으로 설명을 조회한다. command 생략 시 지원 목록을 보여준다. 과제 생성 없이 허용된 서버에서 사용 가능하며 본인에게만 응답한다. 분석 상태 및 기록을 변경하거나 모델을 호출하지 않는다. 기존 /help commands와 초기 안내에서 발견할 수 있다.

## 검증

명령 설명·슬래시 정규화·목록·미지원 명령·과제 없이 조회·허용 서버 검사와 기존 답변 흐름 및 제출 결과 포럼 통합 회귀: 113 passed, 19 skipped. 생략 항목은 별도 모델 설정을 요구한다.
