# Discord 봇 실행과 검증

## Codex CLI 연결 선택 (2026-10-07)

Spec048은 기존 구독을 이용하는 Discord 전용 Codex CLI 공급자를 추가한다. DISCORD_LLM_PROVIDER=codex_cli를 명시하면 출제·조회 해석·용어 설명·평가가 CLI를 사용한다. 생략 시 아래 기존 Gemma 구성이 유지된다. CLI 설치·전용 인증·main 배포에서 공급자 전환은 [Codex CLI 설정](codex-cli-setup.md)을 따른다. 실제 검증 범위와 미확인 운영 항목은 [Spec048](../specs/048-discord-codex-cli.md)에 기록한다.

## 현재 운영 배포 — 2026-10-07

현재 운영은 main 커밋 배포 절차로 갱신합니다. 2026-10-07 Spec053 자연어 조회를 main `5062612894ee0457eec57c5fdcdfe5406a9f3b41` 기준으로 배포하고, 원래 스레드의 200행 조회·전체 JSON 다운로드·자료 부족 안내를 확인했습니다. 현재 실행 커밋은 `python scripts/deploy_discord.py --status`로 확인합니다. [자연어 조회 운영 검증](../tests/reports/spec053-natural-language-public-query-2026-10-07.md)을 참고하세요. 출제 수정과 호출 차감을 기존 countTokens·pending 예약·SQL·고급 판단 검사에 통합했습니다. [현재 통합·배포 보고와 재시작 명령](../tests/reports/generation-integration-2026-10-07.md)을 사용합니다. 이전 배포에서 필수 text/practice 선택, 중급 SQL 답장·오류 수정·최종 평가·결과 포럼/PDF·재시작 재개, 고급 완료 분석 연결·검산 설명 평가를 확인한 기록은 [이전 검증](../tests/reports/verification-spec035-036-completion-2026-10-07.md)에 보존합니다. 실제 모델의 분석 전체 흐름과 품질 검증은 별도이며, 아래 과거 경로·미배포 내용은 당시 기록입니다.

## SQL 연습 추가 — 현재 체크아웃 구현

통합 명령 `/training`은 `practice`와 `text`가 필수다. 분석 연습은 text로 Gemma 출제를 요청한다. SQL 연습은 현재 `text:튜토리얼 신규 가입자 3단계 완료율`을 지원하며 다른 내용은 지원 범위를 안내하고 거부한다. `SQL 연습`을 선택하면 봇이 제공한 `sql` 코드 블록 틀을 복사해 작성하고, 그 메시지에 답장으로 보내 실행한다. 수정은 전체 SQL을 새 답장으로 제출한다. 메시지 편집은 자동 재실행하지 않는다. 메시지 내용 수신이 제한되면 봇 멘션을 포함해 답장하거나 `/sqlrun text:코드블록`을 사용한다. 설명 포함 최대 1,900자이며 빈 틀·여러 블록은 실행하지 않는다.

성공 실행 후 `/submit`으로 항목별 평가를 받는다. 최신 실행이 실패했다면 `/submit execution_id:이전성공ID`로 명시 선택한다. `/help kind:SQL 해설 공개`는 첫 풀이 제출 이후만 가능하며 노출 이력을 남긴다. 분석 연습은 `practice:분석 연습`으로 기존 흐름을 사용한다. 기존 튜토리얼 완료 분석은 `practice:SQL 연습 text:튜토리얼 완료율 source_session_id:과제ID`로 연결한다. 고급 SQL은 코드 블록 밖에 중복·기간·분모/NULL 검산 방법을 설명한다. 실제 수행하지 않은 추가 검산은 계획으로 표시한다. 텍스트로 생성한 분석 과제의 SQL 검산 계약은 아직 지원하지 않는다.

2026-10-06 당시 문서상 운영 Compose 경로는 `D:\Codex\DA-Agent`였으며 해당 시점에는 배포하지 않았다. 현재 실제 설정과 배포 경로는 문서 상단의 최신 기록을 따른다. 아래의 과거 운영 검증은 새 SQL 연습의 실제 Discord 검증을 의미하지 않는다. 운영 반영 시 변경 코드로 이미지를 빌드하고 명령 동기화를 확인한 뒤 부모 채널에서 **SQL 연습 선택 → 코드 블록 답장 → 실행 결과 표시**를 직접 확인한다. 핵심 실제 흐름과 게시·재개가 확인되어야 Spec의 화면 항목을 완료로 갱신한다. [Spec044](../specs/044-discord-sql-practice-mode.md), [구현 검증 기록](../tests/reports/verification-spec035-2026-10-06.md).

기존 웹 앱과 별도 프로세스·별도 DB에서 동작한다. 웹 라우트·정적 화면·기존 requirements는 변경하지 않는다. Discord 명령이 내부 Python 서비스를 호출하며 웹 API를 외부에 공개하지 않는다.

## 준비

Python 3.12 이상과 기존 프로젝트 의존성을 준비한 **별도 가상환경**에서 추가 의존성을 설치한다.

```powershell
python -m pip install -e . -r requirements-discord.txt
```

Discord Developer Portal에서 Bot을 만들고 서버 설치에 `bot`, `applications.commands` scope를 사용한다. 실제 운영 서버와 참여자는 파일럿 전에 지정한다. 부모 텍스트 채널에서 봇에 채널 보기, 비공개 스레드 생성, 스레드 관리, 스레드 발언, 메시지 기록 읽기 권한을 준다. 참가자는 부모 채널 보기와 스레드 발언 권한이 필요하다. 봇의 관리자 권한은 필요하지 않다. 비공개 스레드는 초대 불가로 만들고 소유자만 추가한다. 서버 관리자 및 스레드 관리 권한자는 비공개 스레드에 접근할 수 있으므로 완전한 비밀 공간으로 안내하지 않는다.

**필수:** Bot → Privileged Gateway Intents → **Server Members Intent**를 켜고 저장한다. 비공개 스레드 참가자 목록 REST 조회는 앱에 이 Intent가 허용돼 있어야 성공한다. Gateway의 전체 멤버 캐시를 켜는 것과는 별개이며 현재 봇은 REST로 소유자·다른 참가자를 검사한다. 꺼져 있으면 과제 데이터는 생성돼도 스레드 검증이 403 Missing Access로 실패한다.

일반 대화를 사용하려면 Developer Portal의 Message Content Intent와 `DISCORD_MESSAGE_CONTENT=true`를 모두 활성화한다. 기본값은 false이며 `/data`, `/help`, `/report`, `/followup`으로 같은 입력을 보낼 수 있다. 스레드 밖·다른 봇·소유자 아닌 사용자의 일반 메시지는 자동 응답하지 않는다.

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
$env:DISCORD_GENERATION_DIRECTORY = '.local/discord-generation'  # 생성 패키지·검산 자료 보관
$env:DISCORD_DAILY_CALL_LIMIT = '30'  # 사용자별 하루 호출 수. 0은 무제한이며 사용량 기록은 유지
$env:DISCORD_MESSAGE_CONTENT = 'false'
python -m da_agent.discord_bot
```

Discord 전용 기록 DB와 전용 데이터 DB를 먼저 준비한다. 관리자 연결은 합성 과제 적재에만 쓰고 learner 계정에는 과제 데이터 읽기만 허용한다. `records`, `training`, `postgres` DB 이름과 기록·데이터 동일 DB는 시작 설정에서 차단한다. 운영자는 웹이 사용자 정의 DB를 사용한다면 해당 DB와도 분리해야 한다. 서비스의 데이터 준비 계약·DB 권한을 배포 전에 검증한다. Bot은 설정된 서버에만 명령을 등록하며, 매 이벤트에서도 allowlist를 검사한다.

## Discord 안의 흐름

### 텍스트 출제 설정과 검증 범위

Spec045 코드는 고정 topic을 필수 text·difficulty로 바꾸고 기존 adaptive의 설계·합성 자료·독립 DB 검산을 연결한다. Discord 출제·검토·조회 해석·교육·평가는 모두 DISCORD_MODEL의 Gemma API를 사용한다. 사용자 요청과 난이도로 가상 업무 문제를 직접 설계하며 실제 사례 검색·출처 확인은 수행하지 않는다. DISCORD_SEARCH_MODEL·GEMINI_SEARCH_MODEL은 Discord에서 사용하지 않는다. 다른 모델이나 tutorial 문제로 자동 대체하지 않는다. 설계·수정·검토는 요청별 최대8회와 사용자 일일 한도를 함께 적용한다.

JSON 형식 오류 진단이 필요하면 `DISCORD_JSON_DIAGNOSTICS_DIR`을 서버 전용 `.local/diagnostics` 경로로 설정한다. 기본값은 원문 기록 비활성화다. 실패 시 응답 원문·파싱 대상·오류 행/열·HTTP 상태·종료 사유·생성 설정을 기록하며 API 키·요청 메시지는 저장하지 않는다. 원문에는 비공개 과제 설계가 포함될 수 있으므로 Discord 안내나 공개 검증 자료에 붙이지 않는다. 기록 폴더는 Git에서 제외하고 진단 종료 후 운영 설정을 해제한다. 검증 스크립트는 `--diagnostics-dir .local/diagnostics/<실행명>`으로 같은 기능을 켤 수 있다.

준비 중 /answer는 확인 질문 답변, /end는 취소, /resume은 상태 복원이다. 실패·중단·시작 전 요청은 재시도 버튼 또는 /retry로 수동 실행한다.

생성 분석 과제의 `/data`는 자연어로 현재 공개 표의 원본 행·컬럼 선택·필터·정렬·비교·여러 집계를 요청할 수 있다. 예: `shop_profiles의 전체 데이터를 조회해줘`, `사전 숙련도가 beginner인 계정만 보여줘`, `상점 구성별 초기·후기 평균 소모량을 비교해줘`. 해석한 SELECT를 현재 공개 표·컬럼으로 검증하고 기존 읽기 전용 실행기에서 실행한다. 없는 자료는 빠진 항목과 조회 가능한 표·컬럼을 안내하고, 모호한 조건은 확인한다. 결과는 첫 10행 표와 수집한 전체 데이터 JSON 첨부로 제공하며 수집 제한에 도달하면 불완전함을 표시한다. 기존 구조화된 집계 조건과 고정 튜토리얼 지표 계약도 유지한다. 이 확장은 운영에 반영했으며 실제 Discord 스레드에서 전체 조회·JSON 다운로드와 자료 부족 안내까지 확인했다. [Spec053](../specs/053-discord-natural-language-public-query.md), [검증 보고](../tests/reports/spec053-natural-language-public-query-2026-10-07.md)를 참고한다.

생성 자료와 공개 정의는 출제 전에 검산·고정한다. 임의 자연어 보고 수치 전체의 자동 검산은 미지원이며 미검산을 감점으로 만들지 않는다. 해당 정의·난이도·자료·모델·코드의 반복 평가 품질 검증이 없으면 점수는 보류하고 피드백을 제공한다.

Compose에는 discord-generation 영구 볼륨을 추가했다. 배포 시 기존 기록·DB 볼륨을 보존하고 Gemma 키·모델 준비 → 이미지 빌드·재시작 → 서버 명령 동기화를 확인한다. 이번 작업은 운영 봇을 재시작하거나 명령을 동기화하지 않았다. [Spec045 검증 보고](../tests/reports/verification-spec036-2026-10-06.md)를 따른다.

2026-10-06 현재 이 PC의 독립 Docker 실행 환경을 준비했고, 서버 1556888486919934064의 `#da-agent`(1556888698992468039)에 접근·권한 확인 및 10개 명령 등록을 마쳤다. 실행·중지 명령과 비밀 파일 배치는 [독립 실행 Spec](../specs/027-discord-isolated-runtime.md)에 기록했다. PC와 Docker가 실행되는 동안 봇이 동작한다. 일반 메시지 Intent는 꺼져 있으므로 아래 슬래시 명령을 사용한다.

1. Spec045 코드 반영·명령 동기화 후에는 부모 채널에서 `/training practice:analysis text:튜토리얼 완료율 하락을 분석하고 싶어 difficulty:intermediate`를 실행한다. text는 필수이며 help_level 선택은 유지한다. 기존 이미지로 실행 중인 봇은 이전 topic 형식을 사용한다.
2. 최초 Interaction을 바로 지연 응답하고, 과제 준비 뒤 비공개 과제 스레드와 업무 안내를 보낸다.
3. 스레드에서 `/data text:...`로 새 조회를 요청한다. 확인 질문에는 답장·봇 멘션 또는 `/answer text:...`로 답한다.
4. `/help text:...`로 도움을 요청한다. `/sql execution_id:...`로 실제 실행 SQL을 보고 `/evidence execution_id:...`로 보고 근거를 선택한다. 이 두 명령이 버튼 대안이다.
5. `/data text:...`는 새 조회를 시작한다. 봇 확인 질문에는 해당 메시지에 답장하거나 멤버 목록의 `@DA-Agent` 봇을 선택해 멘션과 함께 답한다. `/answer text:...`로도 현재 질문에 답할 수 있다. 내용 수신이 제한된 답장은 멘션 또는 `/answer` 사용 안내가 나온다. 과거 질문에 대한 답장은 최신 질문으로 안내하며 일반 채팅은 자동 조회하지 않는다.
6. `/report text:...`로 초안·수정본을 저장하고 긴 보고는 `/report text:... append:true`로 이어 쓴다. `/help kind:분석 방향` 또는 `kind:중간 검토`로 도움 종류를 선택할 수 있다. `/help`의 `kind`에서 데이터 사전·평가 기준·전체 명령을 선택하면 질문 입력 없이 공개 자료를 확인할 수 있다. 첫 안내에는 업무 요청, 주별 가입 대상, UTC 관측 경계, 데이터의 필수 사실과 다음 행동만 표시한다. 보고 후속 질문에도 답장·멘션·`/answer` 또는 `/followup text:...`로 답할 수 있다. `/submit`으로 최종 제출한다.
7. `/end`로 중단한다. `/resume session_id:...`로 재개하며 ID 생략 시 서비스가 자신의 최근 과제를 찾는다. 스레드 삭제 시 부모 채널에서 `/resume`을 실행하면 기록을 유지하고 새 비공개 공간에 연결한다. 접근 권한 오류는 기존 연결을 보존하고 운영자의 권한 수정을 안내한다.

응답은 Discord 길이 제한보다 작은 1,900자 단위로 나눈다. 서비스·모델·사용자 문구는 Markdown을 이스케이프하고 모든 전송에 mentions 비활성화를 적용한다. 15분 이후 Interaction 토큰이 만료되면 이미 저장한 과제 스레드 응답과 `/resume`으로 확인한다. 원시 예외·접속 정보는 메시지에 포함하지 않는다.

## 검증 결과와 남은 확인

2026-10-06: 격리 컨테이너에서 `discord.py==2.7.1`을 설치한 뒤 `python -m pytest tests/automated/test_discord_transport.py -q` 실행: **20개 통과**. 실제 SDK 명령 10개 등록과 기본 Intent를 로그인 없이 확인했고, 모의 전송으로 소유권·허용 서버·권한 실패·전용 공간 연결·삭제 복구·길이·멘션·오류 비밀 차단을 확인했다. 실제 Discord 로그인과 Gemini 호출은 수행하지 않았다.

실제 서버에서 사용자 A가 `/training`을 실행한 뒤 비공개 스레드의 업무 안내를 확인하는 것이 첫 화면 확인 행동이다. 이후 A의 조회→도움→보고→후속 답변→제출, 사용자 B의 분리와 조작 차단, 봇 재시작·보관·삭제·권한 부족·Intent 비활성 흐름을 확인하고 `tests/reports/`에 남겨야 한다. 실제 서버 검증 및 사람의 교육 품질 승인이 끝나기 전에는 출시 완료로 표시하지 않는다.

공식 근거: [Discord Interaction 응답과 만료](https://docs.discord.com/developers/interactions/receiving-and-responding), [스레드 권한](https://docs.discord.com/developers/topics/threads), [discord.py 변경 기록](https://discordpy.readthedocs.io/en/stable/whats_new.html), [Interactions API](https://discordpy.readthedocs.io/en/stable/interactions/api.html).

## 중단된 대기 응답 정리

정상 요청은 원래 비공개 대기 응답을 완료 안내로 수정한다. 취소·종료·재시작 시 원래 응답을 “요청의 응답이 중단되었습니다”와 `/resume` 안내로 교체한다. 임시 응답 복구 정보는 전용 `discord-responses` Docker 볼륨에 보관하며 완료·복구·15분 만료 후 삭제한다. 단독 실행에서는 `.local/discord-responses`를 사용하고 `DISCORD_RESPONSE_DIRECTORY`로 경로를 지정할 수 있다. 이미 토큰이 만료된 과거 메시지와 기능 적용 전에 복구 정보를 저장하지 않은 응답은 수정할 수 없다. [구현·검증 기록](../specs/036-discord-interrupted-response.md).

## 명령어 사용법 조회

`/tip command:report`로 명령어의 목적·입력 옵션·사용 예시를 확인한다. `/tip command:/report`도 지원하며 `/tip`만 실행하면 전체 명령 목록을 보여준다. 허용된 서버에서 과제 없이 사용할 수 있고 본인에게만 응답한다. 분석 기록·질문 상태·모델 호출에 영향을 주지 않는다.

## 모르는 분석 용어 질문

본인의 과제 스레드에서 `/question text:ads와 organic이 뭐야?`로 질문하면 용어당 최대 3줄로 한국어 뜻·예시를 설명한다. 한 번에 최대 5개 용어, 질문은 500자 이내로 입력한다. ads·organic·코호트·리텐션 등 기본 용어는 API 없이 설명하고, 그 밖의 용어는 기존 일일 한도 내에서 모델을 1회 호출한다. `/tip command:question`으로 사용법을 확인한다.

용어 설명은 진행 중인 확인 질문에 대한 답변으로 해석하지 않으며 기존 조회 조건·보고·평가를 유지한다. 완료·중단된 과제에서도 사용할 수 있다. 질문과 설명은 대화·도움 기록으로 저장된다. 모델 오류나 한도 도달 시에는 재질문 안내를 제공한다. [Spec 및 검증 기록](../specs/039-discord-term-question.md).

## 데이터 사전과 조회 표 (2026-10-06)

데이터 사전과 성공 조회의 첫 10행을 한글 PNG 표로 첨부한다. 표를 누르면 Discord 미디어 뷰어에서 확대할 수 있다. 기호·공백으로 열을 정렬하지 않는다. 숫자는 오른쪽 정렬하고 NULL·빈 결과·표시 제한·불완전 수집을 구분한다. 계산 기준은 한글 목록으로, 실행 ID·SQL·근거 선택은 기존 명령으로 제공한다. 이미지 대체 설명도 포함한다.

- `/data text:데이터 사전 보여줘`, `/help text:users 데이터 사전 알려줘`, `@DA-Agent 데이터 사전 보여줘`로 공개 사전을 조회한다. 모델·SQL을 호출하지 않으며 진행 중인 조회 확인 조건을 유지한다.
- 자연어 SQL 조회가 성공하면 결과 표를 자동으로 첨부한다. 첫 안내는 간결하게 표시한다. 사전 표는 `/help`의 데이터 사전 선택이나 사전 조회 요청으로 확인한다.
- 봇에 파일 첨부(Attach Files) 권한이 필요하다. 첨부 실패 시 행별 목록으로 값을 제공하며 성공 조회를 다시 실행하지 않는다.
- `requirements-discord.txt`에 Pillow를 포함하며 Dockerfile.discord에서 Noto CJK 글꼴을 설치한다. Windows는 맑은 고딕을 사용한다. 다른 실행 환경은 `DISCORD_TABLE_FONT`와 선택 사항인 `DISCORD_TABLE_BOLD_FONT`에 한글 글꼴 경로를 설정한다.

Discord 관련 자동 테스트 115개 통과, 전용 테스트 DB가 필요한 19개는 생략. 실제 과제 스레드에서 자연어 사전 조회, 채널별 완료율 조회, 표 수신, 사전 표 확대를 확인했다. 근거는 [표 검증 기록](../tests/reports/discord-tables-2026-10-06.md)에 있다.


## 최종 제출 결과 포럼 (2026-10-06)

현재 운영 반영은 [main 커밋 배포 절차](discord-release-workflow.md)를 따른다. 과거 문서의 체크아웃·이미지 태그를 직접 빌드하거나 재사용하지 않는다.

완료 과제의 보고서·평가 카드가 모두 게시되면 과제 공간과 부모 채널에 결과 링크를 남기고 비공개 과제 스레드를 **보관·잠금**한다. 대화·첨부파일은 보존하며 평가 보류·게시 실패는 보관하지 않는다. 봇에는 스레드 관리 및 발언 권한이 필요하다. 실패하면 `/resume session_id:...`으로 게시·보관을 재시도한다. 완료 과제의 `/resume`은 결과와 과거 대화 링크를 안내하며 보관·잠금을 해제하지 않는다. 이전에 이미 삭제된 완료 스레드는 복구하거나 새로 만들지 않는다. 완료 과제에 대한 의견은 포럼 댓글에서 이어간다. [보관과 제목 정책](../specs/043-discord-archived-training-and-titles.md).

새 과제 제목은 `[중급 - 튜토리얼 완료율 변화와 우선 대응]`처럼 한국어 난이도와 짧은 문제명으로 생성한다. 전체 40자 이내이며 긴 문제명은 `…`로 줄인다. 같은 사용자·서버·부모 채널에서 같은 표시 제목이 반복되면 `[중급 #2 - 문제명]`처럼 앞쪽에 순번을 표시하고 다른 난이도는 별도로 센다. 이름을 DB에 예약하므로 동시 생성·재시도·진행 과제 복구에서도 안정적으로 유지한다. 기존 ID 제목은 일괄 변경하지 않는다.

`/history`는 본인의 현재 서버 연습 기록을 5개씩 보여준다. 생성일(한국 날짜)·상태·최신 점수/평가 보류·결과 링크·과제 재개 명령을 확인할 수 있다. 다음 기록은 `/history page:2`로 조회한다. 기록이나 모델 평가를 변경하지 않는다.

`/submit`은 제출과 평가를 저장한 뒤 서버의 `DA-Result` 포럼에 결과 글을 게시한다. Discord 화면에서는 `da-result`로 정규화될 수 있다. 기존 포럼 이름은 대소문자 구분 없이 검색한다. 없으면 과제 부모 채널과 같은 범주·권한으로 생성한다. 같은 이름의 일반 채널만 있으면 자동 변환하거나 중복 생성하지 않고 포럼을 준비하도록 안내한다.

2026-10-07 Spec055 변경 코드에서는 **첫 포스트 메시지의 요약 카드 하나**에 결론, 최대 3개 근거, 평가 상태·점수·잘한 점·개선점, 다음 행동을 900자 이하로 표시한다. 추가 AI 호출 없이 저장된 제출·평가에서 발췌하며 별도 해석 한계, 평가 보류, 확인된 계산 오류를 표시한다. 전체 제출·후속 답변·항목별 평가·SQL·근거 자료는 같은 메시지의 PDF 다운로드 버튼/첨부에 보존한다. 평가 답글을 새로 보내지 않으며 기존 회원 댓글은 보존한다. 원시 평가 JSON과 전체 대화·비공개 생성 정보는 게시하지 않는다. 원래 과제에는 짧은 상태와 게시글 링크를 남긴다. 기존 결과는 `/resume` 때 같은 첫 메시지가 갱신된다. 운영 반영·화면 확인 상태는 [Spec055](../specs/055-discord-compact-result-summary.md)를 따른다.

분석 과제의 `PDF 다운로드`는 3쪽 학습형 결과(요약·분석 근거·평가 피드백)를, `상세 원문 PDF`는 전체 보고·표·평가 인용을 제공한다. 기존 글은 `/resume`으로 요약 첨부를 보완하며 원문을 보존한다. SQL 연습의 전체 PDF 형식은 유지한다. [Spec056](../specs/056-discord-concise-result-pdf-template.md)에서 PDF 구성과 운영 검증을 확인한다.

평가 하나당 결과 게시글 하나를 저장한다. 완료된 과제에서 `/submit`을 다시 실행하면 재평가 없이 같은 글의 첫 메시지를 복구한다. 평가가 보류된 과제의 `/submit`은 기존 방식대로 평가를 다시 요청하며 별도 평가 결과 글을 만든다. 게시만 복구하고 싶으면 `/resume session_id:...`을 사용한다. 부분 게시 실패 시 저장된 글의 첫 메시지와 PDF를 갱신하고, 생성 성공 여부가 불명확하면 확인 없이 새 글을 만들지 않는다. 이전 방식의 평가 답글과 회원 댓글은 보존한다. 게시 오류와 평가 오류는 구분하며 저장된 평가를 취소하지 않는다. 실패 시 원래 비공개 스레드에도 읽기 쉬운 결과 카드를 제공한다.

봇은 기존 포럼에서 채널 보기·게시글 만들기(Send Messages)·스레드 발언·링크 삽입·기록 읽기 권한, 새 포럼 생성에는 별도의 채널 관리 권한이 필요하다. 기존 포럼의 접근 권한은 변경하지 않으며 제출자가 읽을 수 없는 포럼에 게시하지 않는다. 결과가 포럼 독자에게 공개되므로 참가자·운영진 역할을 통한 접근 범위를 먼저 정해 둔다. 태그가 필수인 포럼은 봇이 사용할 수 있는 태그 하나를 적용한다.

이 PC의 서버에서는 봇 자동 생성이 권한 부족으로 거부되어 관리자 화면으로 포럼 1556918941576724581을 생성했다. 봇 권한은 확대하지 않았다. 기존 제출 결과를 실제 `/submit`으로 게시하고 재실행·링크 이동·카드 표시·REST 내용을 확인했다. 테스트 128개 통과 / 19개 전용 DB 테스트 생략. [검증 기록](../tests/reports/discord-results-2026-10-06.md) 참조.

## 출제 수정·입력 한도 보완 (2026-10-07)

출제 수정은 최신 초안 하나와 현재 오류·허용 표 관계만 전달하며 전체 실패 이력은 비공개로 보존한다. 내부 입력 크기 검사와 65초 공유 예산을 통과한 외부 전송 시도만 출제 요청/일일 추론 호출 횟수에 반영한다. 분당 예산 부족은 대기 시간을 안내하며 /retry는 대기 뒤 수동 실행한다. 내부 입력 초과·인증 설정·요청별 호출 소진은 원인을 해결해야 하므로 무조건 재시도 버튼을 제공하지 않는다. 일일 제한은 다음 날 재시도한다. 사용자 목표·난이도·계정 분모·관계 검증은 유지한다.

DISCORD_API_BUDGET_DIR은 기본으로 DISCORD_GENERATION_DIRECTORY/api-budget을 사용한다. SQLite 공유 예산은 기존 generation 볼륨에 보존하며 4열/5열 DB를 모두 지원한다. 실제 호출 전에 최대130초 예산 회복을 기다리고, 기다리는 중에는 추론 호출 수를 차감하지 않는다. 다른 배포 체크아웃은 보존하고 통합한 현재 코드로 운영 봇을 갱신했다. [Spec046](../specs/046-discord-bounded-generation-repair.md), [현재 검증 보고](../tests/reports/generation-integration-2026-10-07.md).

조회 명령어 변경 (2026-10-08): `/query`를 `/data`로 변경했다. 위 사용 예시는 수정된 코드 기준이며, 운영 봇의 명령어는 배포와 동기화 후 바뀐다.
