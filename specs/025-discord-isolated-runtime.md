# 025 Discord 서버 연결과 독립 실행

## 구현 진행도

- 진행도: 2/3개 완료 (66.7%)
- 마지막 갱신일: 2026-10-06
- 남은 작업: Discord 화면에서 훈련 시작
- 차단 사유: 없음. 사용자 화면에서 /training 실행 확인 대기.

| 작업 | 상태 | 완료 조건 | 관련 코드 / 검증 결과 |
| --- | --- | --- | --- |
| 독립 DB·실행 이미지 준비 | 완료 | 전용 DB 초기화, learner 조회 성공·수정 차단, 기존 웹 정상 | `compose.discord.yaml`, `Dockerfile.discord`; 40명 데이터 조회와 수정 권한 차단; 웹 health ok |
| 실제 Gateway·명령 등록 | 완료 | 지정 서버에 로그인하고 10개 명령 등록, 부모 채널 권한 확인 | `scripts/run_discord_container.py`; Gateway ready, 10개 명령 등록, 채널 필수 권한 모두 true; SDK 시작 회귀 포함 22 tests passed |
| 실제 화면에서 첫 훈련 | 검증 대기 | 지정 채널의 /training으로 비공개 과제 안내 확인 | 사용자 화면 확인 필요 |

## 요구사항

- 기존 웹 Compose 프로젝트와 다른 `da-agent-discord` 프로젝트, 네트워크, PostgreSQL 볼륨을 사용한다. DB 포트와 웹 포트를 외부에 노출하지 않는다.
- 기록 DB와 과제 DB를 분리하고, 적재 관리자와 학습자 계정도 분리한다.
- 토큰과 API 키는 호스트 파일을 읽기 전용 마운트하며 이미지·Git에 포함하지 않는다. DB 비밀번호는 Git에서 제외된 `.local/discord-runtime/`에 보관한다.
- 서버 ID는 1556888486919934064, 확인 대상 부모 채널 ID는 1556888698992468039이다. 현재 제품은 서버만 허용 목록으로 제한하며 채널 전용 제한은 구현하지 않는다.
- Message Content Intent는 꺼 두고 슬래시 명령으로 입력한다. Gemma 모델은 gemma-4-26b-a4b-it, 1일 사용자별 호출 한도는 30이다.
- Docker가 실행되는 동안 컨테이너를 자동 재시작한다. PC와 Docker가 중지되면 봇도 중지된다.

## 실행·중지

작업 폴더는 `C:\Users\Administrator\.codex\worktrees\discord-mvp\DA-Agent`이다. 이 PC에 생성된 비밀 설정이 있어야 실행된다.

```powershell
docker compose --project-name da-agent-discord --env-file .local/discord-runtime/compose.env -f compose.discord.yaml up -d
docker compose --project-name da-agent-discord --env-file .local/discord-runtime/compose.env -f compose.discord.yaml stop
```

볼륨 제거 옵션을 사용하면 학습 기록이 삭제되므로 재시작에는 stop/up을 사용한다. 아직 두 사용자 전체 흐름과 교육 품질 승인이 끝나지 않았으므로 출시 완료로 보지 않는다.
