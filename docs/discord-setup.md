# Discord 봇 실행과 검증

기존 웹 앱과 별도 프로세스·별도 DB에서 동작한다. 웹 라우트·정적 화면·기존 requirements는 변경하지 않는다. Discord 명령이 내부 Python 서비스를 호출하며 웹 API를 외부에 공개하지 않는다.

## 준비

Python 3.12 이상과 기존 프로젝트 의존성을 준비한 **별도 가상환경**에서 추가 의존성을 설치한다.

```powershell
python -m pip install -e . -r requirements-discord.txt
```

Discord Developer Portal에서 Bot을 만들고 서버 설치에 `bot`, `applications.commands` scope를 사용한다. 실제 운영 서버와 참여자는 파일럿 전에 지정한다. 부모 텍스트 채널에서 봇에 채널 보기, 비공개 스레드 생성, 스레드 관리, 스레드 발언, 메시지 기록 읽기 권한을 준다. 참가자는 부모 채널 보기와 스레드 발언 권한이 필요하다. 봇의 관리자 권한은 필요하지 않다. 비공개 스레드는 초대 불가로 만들고 소유자만 추가한다. 서버 관리자 및 스레드 관리 권한자는 비공개 스레드에 접근할 수 있으므로 완전한 비밀 공간으로 안내하지 않는다.

**필수:** Bot → Privileged Gateway Intents → **Server Members Intent**를 켜고 저장한다. 비공개 스레드 참가자 목록 REST 조회는 앱에 이 Intent가 허용돼 있어야 성공한다. Gateway의 전체 멤버 캐시를 켜는 것과는 별개이며 현재 봇은 REST로 소유자·다른 참가자를 검사한다. 꺼져 있으면 과제 데이터는 생성돼도 스레드 검증이 403 Missing Access로 실패한다.

일반 대화를 사용하려면 Developer Portal의 Message Content Intent와 `DISCORD_MESSAGE_CONTENT=true`를 모두 활성화한다. 기본값은 false이며 `/query`, `/help`, `/report`, `/followup`으로 같은 입력을 보낼 수 있다. 스레드 밖·다른 봇·소유자 아닌 사용자의 일반 메시지는 자동 응답하지 않는다.

## 별도 환경변수

토큰·DB 비밀번호·Gemini 키는 서버 비밀 저장소에 보관하고 저장소에 커밋하지 않는다. 아래는 값 형식이며 실제 비밀이 아니다. 기존 웹의 `RECORDS_DSN`, `LEARNER_DSN`, `GEMINI_API_KEY_FILE` 기본값으로 대체하지 않는다.

```powershell
$env:DISCORD_BOT_TOKEN = '<Discord bot token>'
$env:DISCORD_GUILD_IDS = '123456789012345678'  # 여러 서버는 쉼표로 구분
$env:DISCORD_RECORDS_DSN = 'postgresql://discord_recorder:<password>@127.0.0.1:5432/discord_records'
$env:DISCORD_ADMIN_DSN = 'postgresql://discord_admin:<password>@127.0.0.1:5432/discord_training'
$env:DISCORD_LEARNER_DSN = 'postgresql://discord_learner:<password>@127.0.0.1:5432/discord_training'
$env:DISCORD_GEMINI_KEY_FILE = 'C:\secrets\discord-gemini.key'
$env:DISCORD_MODEL = 'gemma-4-26b-a4b-it'  # Discord 전용, 웹 GEMINI_MODEL 유지
$env:DISCORD_DAILY_CALL_LIMIT = '30'  # 파일럿 운영자가 예산에 맞게 조정
$env:DISCORD_MESSAGE_CONTENT = 'false'
python -m da_agent.discord_bot
```

Discord 전용 기록 DB와 전용 데이터 DB를 먼저 준비한다. 관리자 연결은 합성 과제 적재에만 쓰고 learner 계정에는 과제 데이터 읽기만 허용한다. `records`, `training`, `postgres` DB 이름과 기록·데이터 동일 DB는 시작 설정에서 차단한다. 운영자는 웹이 사용자 정의 DB를 사용한다면 해당 DB와도 분리해야 한다. 서비스의 데이터 준비 계약·DB 권한을 배포 전에 검증한다. Bot은 설정된 서버에만 명령을 등록하며, 매 이벤트에서도 allowlist를 검사한다.

## Discord 안의 흐름

2026-10-06 현재 이 PC의 독립 Docker 실행 환경을 준비했고, 서버 1556888486919934064의 `#da-agent`(1556888698992468039)에 접근·권한 확인 및 10개 명령 등록을 마쳤다. 실행·중지 명령과 비밀 파일 배치는 [독립 실행 Spec](../specs/025-discord-isolated-runtime.md)에 기록했다. PC와 Docker가 실행되는 동안 봇이 동작한다. 일반 메시지 Intent는 꺼져 있으므로 아래 슬래시 명령을 사용한다.

1. 부모 텍스트 채널에서 `/training topic:tutorial difficulty:intermediate`를 실행한다. 난이도와 별개로 `help_level`을 안내 포함/내 정의 먼저 중 선택할 수 있다.
2. 최초 Interaction을 바로 지연 응답하고, 과제 준비 뒤 비공개 과제 스레드와 업무 안내를 보낸다.
3. 스레드에서 `/query text:...` 또는 일반 대화로 분석한다. 확인 질문 답변도 `/query`로 보낼 수 있다.
4. `/help text:...`로 도움을 요청한다. `/sql execution_id:...`로 실제 실행 SQL을 보고 `/evidence execution_id:...`로 보고 근거를 선택한다. 이 두 명령이 버튼 대안이다.
5. `/report text:...`로 초안·수정본을 저장하고 긴 보고는 `/report text:... append:true`로 이어 쓴다. `/help kind:분석 방향` 또는 `kind:중간 검토`로 도움 종류를 선택할 수 있다. `/followup text:...`로 업무 담당자의 질문에 답한다. `/submit`으로 최종 제출한다.
6. `/end`로 중단한다. `/resume session_id:...`로 재개하며 ID 생략 시 서비스가 자신의 최근 과제를 찾는다. 스레드 삭제 시 부모 채널에서 `/resume`을 실행하면 기록을 유지하고 새 비공개 공간에 연결한다. 접근 권한 오류는 기존 연결을 보존하고 운영자의 권한 수정을 안내한다.

응답은 Discord 길이 제한보다 작은 1,900자 단위로 나눈다. 서비스·모델·사용자 문구는 Markdown을 이스케이프하고 모든 전송에 mentions 비활성화를 적용한다. 15분 이후 Interaction 토큰이 만료되면 이미 저장한 과제 스레드 응답과 `/resume`으로 확인한다. 원시 예외·접속 정보는 메시지에 포함하지 않는다.

## 검증 결과와 남은 확인

2026-10-06: 격리 컨테이너에서 `discord.py==2.7.1`을 설치한 뒤 `python -m pytest tests/automated/test_discord_transport.py -q` 실행: **20개 통과**. 실제 SDK 명령 10개 등록과 기본 Intent를 로그인 없이 확인했고, 모의 전송으로 소유권·허용 서버·권한 실패·전용 공간 연결·삭제 복구·길이·멘션·오류 비밀 차단을 확인했다. 실제 Discord 로그인과 Gemini 호출은 수행하지 않았다.

실제 서버에서 사용자 A가 `/training`을 실행한 뒤 비공개 스레드의 업무 안내를 확인하는 것이 첫 화면 확인 행동이다. 이후 A의 조회→도움→보고→후속 답변→제출, 사용자 B의 분리와 조작 차단, 봇 재시작·보관·삭제·권한 부족·Intent 비활성 흐름을 확인하고 `tests/reports/`에 남겨야 한다. 실제 서버 검증 및 사람의 교육 품질 승인이 끝나기 전에는 출시 완료로 표시하지 않는다.

공식 근거: [Discord Interaction 응답과 만료](https://docs.discord.com/developers/interactions/receiving-and-responding), [스레드 권한](https://docs.discord.com/developers/topics/threads), [discord.py 변경 기록](https://discordpy.readthedocs.io/en/stable/whats_new.html), [Interactions API](https://discordpy.readthedocs.io/en/stable/interactions/api.html).

## 중단된 대기 응답 정리

정상 요청은 원래 비공개 대기 응답을 완료 안내로 수정한다. 취소·종료·재시작 시 원래 응답을 “요청의 응답이 중단되었습니다”와 `/resume` 안내로 교체한다. 임시 응답 복구 정보는 전용 `discord-responses` Docker 볼륨에 보관하며 완료·복구·15분 만료 후 삭제한다. 단독 실행에서는 `.local/discord-responses`를 사용하고 `DISCORD_RESPONSE_DIRECTORY`로 경로를 지정할 수 있다. 이미 토큰이 만료된 과거 메시지와 기능 적용 전에 복구 정보를 저장하지 않은 응답은 수정할 수 없다. [구현·검증 기록](../specs/031-discord-interrupted-response.md).
