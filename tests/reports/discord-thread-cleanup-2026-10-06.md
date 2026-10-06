# Discord 완료 과제 스레드 삭제 검증

> 이 문서는 당시 삭제 검증의 이력이다. 이후 사용자 요청으로 삭제 정책을 보관·잠금으로 바꾸었다. 현재 정책과 검증은 [보관 검증 기록](discord-archived-training-2026-10-06.md)을 따른다. 연결된 진단 스크립트 역시 현재 보관 동작을 확인하도록 변경됐다.

- 날짜: 2026-10-06 Asia/Seoul
- 자동 검증: 결과·전송 테스트 53개 통과. 전체 465개 통과·65개 환경 의존 테스트 생략. 기존 FastAPI TestClient 의존성 경고 1개.
- 실패 검증: 포럼 게시, 부모 안내, 명령 응답 실패 시 삭제하지 않음. 삭제 실패 시 완료 게시·평가 유지. 평가 보류 스레드 보존. 삭제된 완료 과제 재개 시 재생성·재평가 없음.
- 운영 봇: `da-agent-discord-bot-1`. 운영 이미지에는 이 작업 브랜치에 아직 없는 PDF 기능이 추가되어 있었다. 4개 모듈에 이번 변경만 병합해 PDF 등록·생성·첨부 검증을 보존하고 봇 재시작. 재시작 후 ready 확인.
- 운영 이미지: `da-agent-discord-bot:pdf-20261006`에 변경 스냅샷 저장. 이전 이미지는 `da-agent-discord-bot:before-thread-cleanup-20261006`에 보존. 컨테이너 파일 백업은 `.local/thread-cleanup-backup`, 운영 병합 파일은 `.local/thread-cleanup-runtime`에 보존했다.
- 실행 환경의 compose 프로젝트 루트는 다른 체크아웃인 `C:/Users/Administrator/.codex/worktrees/discord-mvp/DA-Agent`이다. 해당 소스 체크아웃은 수정하지 않았다. 그 체크아웃에서 이미지를 다시 빌드하면 이 변경을 먼저 병합해야 한다. 현재 체크아웃을 빌드할 때도 별도 PDF 변경과 통합해야 PDF 기능을 포함할 수 있다.

## 실제 동작

- 확인 대상: 기존 완료 세션 `8c4d9a7d-32a6-4ade-b238-b30fdc18ce4b`. 일괄 삭제하지 않았다.
- 실제 SDK의 포럼 전체 게시 확인·부모 채널 안내·삭제 흐름 실행 후 과제 스레드 `1556910756476362772`가 NotFound로 조회됨.
- 결과 글 `1556919302165364747` 동일. 게시 상태 `published`, PDF 첨부 유지, 첫 카드의 끊기는 과제 링크 제거.
- DB의 reports·evaluations·messages·telemetry·thread_id를 실행 전후 비교하여 동일함을 확인. 평가 모델을 호출하지 않음.
- 부모 채널 안내 메시지 `1556950113253982290`에 결과 링크·과제 ID 포함. 브라우저 사이드바에서 삭제한 과제만 사라지고 다른 과제는 유지됨.
- 진단 스크립트의 명령 응답은 가상 캡처를 사용했다. 그 뒤 실제 브라우저에서 부모 채널 `/resume session_id:8c4d9a7d-32a6-4ade-b238-b30fdc18ce4b` 실행하여 개인 명령 응답의 결과 링크 및 “과제 스레드를 다시 만들지 않습니다” 안내 확인. 링크 클릭으로 기존 결과 포럼 이동 확인.
- 화면: [실제 재개 확인](../artifacts/discord-thread-cleanup-resume-2026-10-06.png).
- 재현 진단: [verify_discord_thread_cleanup.py](../../scripts/verification/verify_discord_thread_cleanup.py). **읽기 전용이 아니다.** 명시한 한 개의 완료·게시 과제를 실제 삭제한다. 이미 삭제된 같은 과제에 다시 실행하는 용도가 아니다.

## 제한

신규 모델 평가부터 slash `/submit` 삭제까지 전체 실서버 시나리오는 실행하지 않았다. 자동 테스트와 기존 완료 결과의 실제 게시·삭제, 실제 slash `/resume`을 확인했다. 실제 권한 부족·연결 장애 유발은 자동 테스트로 검증했다. 삭제한 Discord 대화·첨부는 복구하지 못하며 DB는 Discord의 전체 백업이 아니다.
