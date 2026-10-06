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
3. 스레드에서 `/query text:...`로 새 조회를 요청한다. 확인 질문에는 답장·봇 멘션 또는 `/answer text:...`로 답한다.
4. `/help text:...`로 도움을 요청한다. `/sql execution_id:...`로 실제 실행 SQL을 보고 `/evidence execution_id:...`로 보고 근거를 선택한다. 이 두 명령이 버튼 대안이다.
5. `/query text:...`는 새 조회를 시작한다. 봇 확인 질문에는 해당 메시지에 답장하거나 멤버 목록의 `@DA-Agent` 봇을 선택해 멘션과 함께 답한다. `/answer text:...`로도 현재 질문에 답할 수 있다. 내용 수신이 제한된 답장은 멘션 또는 `/answer` 사용 안내가 나온다. 과거 질문에 대한 답장은 최신 질문으로 안내하며 일반 채팅은 자동 조회하지 않는다.
6. `/report text:...`로 초안·수정본을 저장하고 긴 보고는 `/report text:... append:true`로 이어 쓴다. `/help kind:분석 방향` 또는 `kind:중간 검토`로 도움 종류를 선택할 수 있다. `/help`의 `kind`에서 데이터 사전·평가 기준·전체 명령을 선택하면 질문 입력 없이 공개 자료를 확인할 수 있다. 첫 안내에는 업무 요청, 주별 가입 대상, UTC 관측 경계, 데이터의 필수 사실과 다음 행동만 표시한다. 보고 후속 질문에도 답장·멘션·`/answer` 또는 `/followup text:...`로 답할 수 있다. `/submit`으로 최종 제출한다.
7. `/end`로 중단한다. `/resume session_id:...`로 재개하며 ID 생략 시 서비스가 자신의 최근 과제를 찾는다. 스레드 삭제 시 부모 채널에서 `/resume`을 실행하면 기록을 유지하고 새 비공개 공간에 연결한다. 접근 권한 오류는 기존 연결을 보존하고 운영자의 권한 수정을 안내한다.

응답은 Discord 길이 제한보다 작은 1,900자 단위로 나눈다. 서비스·모델·사용자 문구는 Markdown을 이스케이프하고 모든 전송에 mentions 비활성화를 적용한다. 15분 이후 Interaction 토큰이 만료되면 이미 저장한 과제 스레드 응답과 `/resume`으로 확인한다. 원시 예외·접속 정보는 메시지에 포함하지 않는다.

## 검증 결과와 남은 확인

2026-10-06: 격리 컨테이너에서 `discord.py==2.7.1`을 설치한 뒤 `python -m pytest tests/automated/test_discord_transport.py -q` 실행: **20개 통과**. 실제 SDK 명령 10개 등록과 기본 Intent를 로그인 없이 확인했고, 모의 전송으로 소유권·허용 서버·권한 실패·전용 공간 연결·삭제 복구·길이·멘션·오류 비밀 차단을 확인했다. 실제 Discord 로그인과 Gemini 호출은 수행하지 않았다.

실제 서버에서 사용자 A가 `/training`을 실행한 뒤 비공개 스레드의 업무 안내를 확인하는 것이 첫 화면 확인 행동이다. 이후 A의 조회→도움→보고→후속 답변→제출, 사용자 B의 분리와 조작 차단, 봇 재시작·보관·삭제·권한 부족·Intent 비활성 흐름을 확인하고 `tests/reports/`에 남겨야 한다. 실제 서버 검증 및 사람의 교육 품질 승인이 끝나기 전에는 출시 완료로 표시하지 않는다.

공식 근거: [Discord Interaction 응답과 만료](https://docs.discord.com/developers/interactions/receiving-and-responding), [스레드 권한](https://docs.discord.com/developers/topics/threads), [discord.py 변경 기록](https://discordpy.readthedocs.io/en/stable/whats_new.html), [Interactions API](https://discordpy.readthedocs.io/en/stable/interactions/api.html).

## 중단된 대기 응답 정리

정상 요청은 원래 비공개 대기 응답을 완료 안내로 수정한다. 취소·종료·재시작 시 원래 응답을 “요청의 응답이 중단되었습니다”와 `/resume` 안내로 교체한다. 임시 응답 복구 정보는 전용 `discord-responses` Docker 볼륨에 보관하며 완료·복구·15분 만료 후 삭제한다. 단독 실행에서는 `.local/discord-responses`를 사용하고 `DISCORD_RESPONSE_DIRECTORY`로 경로를 지정할 수 있다. 이미 토큰이 만료된 과거 메시지와 기능 적용 전에 복구 정보를 저장하지 않은 응답은 수정할 수 없다. [구현·검증 기록](../specs/031-discord-interrupted-response.md).

## 명령어 사용법 조회

`/tip command:report`로 명령어의 목적·입력 옵션·사용 예시를 확인한다. `/tip command:/report`도 지원하며 `/tip`만 실행하면 전체 명령 목록을 보여준다. 허용된 서버에서 과제 없이 사용할 수 있고 본인에게만 응답한다. 분석 기록·질문 상태·모델 호출에 영향을 주지 않는다.

## 데이터 사전과 조회 표 (2026-10-06)

데이터 사전과 성공 조회의 첫 10행을 한글 PNG 표로 첨부한다. 표를 누르면 Discord 미디어 뷰어에서 확대할 수 있다. 기호·공백으로 열을 정렬하지 않는다. 숫자는 오른쪽 정렬하고 NULL·빈 결과·표시 제한·불완전 수집을 구분한다. 계산 기준은 한글 목록으로, 실행 ID·SQL·근거 선택은 기존 명령으로 제공한다. 이미지 대체 설명도 포함한다.

- `/query text:데이터 사전 보여줘`, `/help text:users 데이터 사전 알려줘`, `@DA-Agent 데이터 사전 보여줘`로 공개 사전을 조회한다. 모델·SQL을 호출하지 않으며 진행 중인 조회 확인 조건을 유지한다.
- 자연어 SQL 조회가 성공하면 결과 표를 자동으로 첨부한다. 첫 안내는 간결하게 표시한다. 사전 표는 `/help`의 데이터 사전 선택이나 사전 조회 요청으로 확인한다.
- 봇에 파일 첨부(Attach Files) 권한이 필요하다. 첨부 실패 시 행별 목록으로 값을 제공하며 성공 조회를 다시 실행하지 않는다.
- `requirements-discord.txt`에 Pillow를 포함하며 Dockerfile.discord에서 Noto CJK 글꼴을 설치한다. Windows는 맑은 고딕을 사용한다. 다른 실행 환경은 `DISCORD_TABLE_FONT`와 선택 사항인 `DISCORD_TABLE_BOLD_FONT`에 한글 글꼴 경로를 설정한다.

Discord 관련 자동 테스트 115개 통과, 전용 테스트 DB가 필요한 19개는 생략. 실제 과제 스레드에서 자연어 사전 조회, 채널별 완료율 조회, 표 수신, 사전 표 확대를 확인했다. 근거는 [표 검증 기록](../tests/reports/discord-tables-2026-10-06.md)에 있다.


## 최종 제출 결과 포럼 (2026-10-06)

`/submit`은 제출과 평가를 저장한 뒤 서버의 `DA-Result` 포럼에 결과 글을 게시한다. Discord 화면에서는 `da-result`로 정규화될 수 있다. 기존 포럼 이름은 대소문자 구분 없이 검색한다. 없으면 과제 부모 채널과 같은 범주·권한으로 생성한다. 같은 이름의 일반 채널만 있으면 자동 변환하거나 중복 생성하지 않고 포럼을 준비하도록 안내한다.

게시글에는 요약, 제출 보고서 원문, 후속 답변, 평가 항목별 등급·근거·개선 행동, 다음 연습, 학습 관측의 한계를 카드로 표시한다. 원시 평가 JSON과 전체 대화·비공개 생성 정보를 게시하지 않는다. 보고서가 길면 카드 여러 개로 이어 표시한다. 원래 과제에는 짧은 상태와 명확한 게시글 링크를 남긴다. 공개 댓글은 결과 토론 공간이며 개인 훈련 명령 처리는 원래 비공개 과제에서 진행한다.

평가 하나당 결과 게시글 하나를 저장한다. 완료된 과제에서 `/submit`을 다시 실행하면 재평가 없이 같은 글을 복구한다. 평가가 보류된 과제의 `/submit`은 기존 방식대로 평가를 다시 요청하며 별도 평가 결과 글을 만든다. 게시만 복구하고 싶으면 `/resume session_id:...`을 사용한다. 부분 게시 실패 시 저장된 글의 누락 카드만 이어 게시하고, 생성 성공 여부가 불명확하면 확인 없이 새 글을 만들지 않는다. 게시 오류와 평가 오류는 구분하며 저장된 평가를 취소하지 않는다. 실패 시 원래 비공개 스레드에도 읽기 쉬운 결과 카드를 제공한다.

봇은 기존 포럼에서 채널 보기·게시글 만들기(Send Messages)·스레드 발언·링크 삽입·기록 읽기 권한, 새 포럼 생성에는 별도의 채널 관리 권한이 필요하다. 기존 포럼의 접근 권한은 변경하지 않으며 제출자가 읽을 수 없는 포럼에 게시하지 않는다. 결과가 포럼 독자에게 공개되므로 참가자·운영진 역할을 통한 접근 범위를 먼저 정해 둔다. 태그가 필수인 포럼은 봇이 사용할 수 있는 태그 하나를 적용한다.

이 PC의 서버에서는 봇 자동 생성이 권한 부족으로 거부되어 관리자 화면으로 포럼 1556918941576724581을 생성했다. 봇 권한은 확대하지 않았다. 기존 제출 결과를 실제 `/submit`으로 게시하고 재실행·링크 이동·카드 표시·REST 내용을 확인했다. 테스트 128개 통과 / 19개 전용 DB 테스트 생략. [검증 기록](../tests/reports/discord-results-2026-10-06.md) 참조.
