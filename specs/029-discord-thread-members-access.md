# Spec 029 — 비공개 스레드 참가자 조회와 복구

## 구현 진행도

- 진행도: 2/3개 완료 (66.7%)
- 마지막 갱신일: 2026-10-06
- 남은 작업: 사용자 /resume 화면 확인
- 차단 사유: 없음. 사용자 설정 완료 후 실제 API·SDK 검증 성공.

| 작업 | 상태 | 완료 조건 | 관련 코드 / 검증 결과 |
| --- | --- | --- | --- |
| 조회 전 복구·참가 순서 | 완료 | 보관 스레드 복구와 봇 참가 후 참가자 검사, 일반 참가자 혼입 차단 유지 | `discord_bot.py`; 활성/보관 스레드 참가 순서·혼입 차단·403 안내 회귀 포함 28 passed |
| 앱 Intent 활성화와 실제 조회 | 완료 | Server Members Intent 허용 후 실제 thread members endpoint 성공 | 앱 허용 플래그 true; 실제 참가자 2명(소유자·봇), SDK validate_thread와 서비스 resume 응답 검증 성공 |
| 사용자 과제 복구 화면 | 검증 대기 | 저장된 과제를 /resume으로 재개하여 안내 확인 | 기존 과제 1ac939a2-ced5-4519-b2bc-e57a031ebe93을 스레드 1556891645016809593에 재연결. 데이터·실패 이벤트 보존; 사람 화면 확인 대기 |

## 요구사항

- 참가자 조회를 생략하지 않는다. 비공개 과제에 다른 일반 사용자가 있으면 접근을 차단한다.
- 보관된 스레드는 부모 채널 권한 확인 후 보관 해제·잠금 해제, 봇 참가, 참가자 검사를 순서대로 수행한다.
- 참가자 조회 Forbidden은 Server Members Intent·채널 접근 설정과 /resume을 안내하는 안전한 오류로 변환한다. 비밀·원시 예외는 사용자에게 출력하지 않는다.
- Gateway 전체 멤버 캐시 Intent를 켤 필요 없이 앱의 특권 허용 설정이 REST 호출에 적용된다.
- 최초 설치 문서에 Server Members Intent를 필수 항목으로 추가한다.

## 근거

[Discord List Thread Members](https://docs.discord.com/developers/resources/channel#list-thread-members): 해당 REST endpoint는 앱의 GUILD_MEMBERS 특권 Intent 허용 여부에 따라 제한된다.
