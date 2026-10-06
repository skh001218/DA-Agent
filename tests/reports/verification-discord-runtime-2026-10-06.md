# Discord 독립 실행 확인 — 2026-10-06

- 토큰 파일 존재, Discord `/users/@me` 인증 성공. 봇 DA-Agent, ID 1556884921652609034. 토큰 원문 미출력.
- 지정 서버 1556888486919934064 조회 404, 채널 1556888698992468039 조회 403. 서버 설치·접근 확인 대기.
- 별도 Compose 프로젝트 `da-agent-discord`, 전용 PostgreSQL 17.4와 영구 볼륨 생성. 기존 웹 프로젝트 변경 없음.
- 봇 이미지 빌드 성공, 기록 DB 초기화 성공.
- 실제 과제 데이터 준비 후 learner로 users 40행 조회, DELETE 권한 거부 확인. 사전 점검 과제 스키마 제거.
- 기존 웹 `/api/health`: status ok, contract_version v1.
- 서버 설치 후 재확인: 서버·채널 접근 성공, 채널 이름 da-agent. View Channel, Send Messages, Read Message History, Manage Threads, Create Private Threads, Send Messages in Threads 모두 허용.
- 첫 실행에서 SDK `log_level=None`이 TypeError를 발생시켜 `log_handler=None`으로 수정. 실제 SDK 시작에 도달하는 회귀 테스트 추가.
- 수정 후 Gateway ready 확인, 봇 컨테이너 running, restart count 0. 서버에 training/resume/query/help/report/followup/submit/sql/evidence/end 10개 명령 등록 확인.
- 전송·SDK 회귀 테스트 22 passed. 기존 웹 health 재확인 ok.
- 실제 사람 계정의 /training 화면 확인 및 두 사용자 전체 흐름은 대기. Message Content Intent false이므로 일반 문장 대신 슬래시 명령을 사용한다.

## Server Members Intent 설정 후 복구

- 사용자가 앱의 Server Members Intent 활성화 완료를 알림. 실제 앱 플래그에서 허용 확인.
- 기존 과제 스레드 보관 해제·봇 참가 후 참가자 목록 조회 성공. 소유자와 봇 2명 외 참가자 없음.
- 과제 1ac939a2-ced5-4519-b2bc-e57a031ebe93을 기존 스레드 1556891645016809593에 다시 연결. 과제 데이터와 실제 사용자 이벤트 보존.
- 실제 Discord SDK Gateway 로그인 후 validate_thread 통과, 저장 기록의 resume 응답 생성 통과. 진단 로그인은 종료, 운영 봇은 계속 실행.
- 전송 회귀 28 passed. 기존 웹 health ok. 사람 계정 /resume 화면 확인 대기.
