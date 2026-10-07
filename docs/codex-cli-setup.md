# Discord Codex 구독 CLI 설정

Spec048은 Discord의 출제·조회 해석·교육·평가를 공식 Codex CLI로 연결한다. 기존 ChatGPT 앱 로그인 어댑터와는 별도이며, CLI 자체가 저장한 ChatGPT 로그인을 사용한다. 웹 설정은 바꾸지 않는다. 목적은 기존 구독 활용이다. 구독 한도는 계속 적용된다.

## 로컬 연결과 확인

Codex CLI 0.160.0으로 검증한다. 이 PC는 Codex 앱의 native codex.exe가 PATH에 있다. 다른 Windows 환경의 npm 설치는 codex.cmd 래퍼를 만들 수 있다. 이 연결은 shell 래퍼를 실행하지 않으므로 앱의 native 실행 파일을 DISCORD_CODEX_BIN으로 지정한다. Linux/macOS의 공식 codex 실행 파일도 지원한다.

    codex --version
    codex login status
    python scripts/verification/verify_codex_cli.py
    python scripts/verification/verify_codex_cli.py --live

마지막 명령만 실제 구독 사용량을 소비한다. 상태 확인은 호출 성공을 뜻하지 않는다. 이 PC에서 짧은 JSON 호출 성공을 확인했으며 전체 결과는 검증 보고에 기록한다.

| 설정 | 값 |
| --- | --- |
| DISCORD_LLM_PROVIDER | codex_cli (생략 시 기존 gemma) |
| DISCORD_CODEX_BIN | codex 또는 native 실행 파일의 절대 경로 |
| DISCORD_CODEX_HOME | 봇 전용 로그인 저장 폴더. 로컬 생략 시 CLI의 기본 저장소 |
| DISCORD_CODEX_MODEL | 선택 모델. 생략 시 사용자 설정을 제외한 CLI 기본 모델 |
| DISCORD_CODEX_TIMEOUT_SECONDS | 180 (1~600초) |

Codex 모드에는 DISCORD_GEMINI_KEY_FILE과 Gemma API 키가 필요 없다. DISCORD_MODEL의 기존 Gemma 값은 Codex에 전달하지 않는다. 모델을 지정하면 그 모델만 요청하고 다른 모델로 자동 대체하지 않는다.

## 컨테이너 로그인

Dockerfile.discord는 Node 22.16.0과 Codex CLI 0.160.0을 포함한다. 인증정보는 이미지·Git에 넣지 않는다. 봇 전용 폴더를 만들고 아래처럼 해당 폴더를 CODEX_HOME으로 사용하는 공식 CLI 로그인 절차를 완료한다. 구독 요청은 해당 폴더로 다시 확인한다.

    $env:CODEX_HOME = 'C:\secrets\da-agent-codex'
    codex login
    python scripts/verification/verify_codex_cli.py --home 'C:\secrets\da-agent-codex' --live

OS credential store만 사용한 경우 컨테이너에서 그 로그인에 접근할 수 없다. 컨테이너와 공유할 이 전용 CLI 저장소에는 cli_auth_credentials_store="file" 설정을 사용하거나 Linux 컨테이너에서 직접 codex login --device-auth를 수행한다. 토큰 갱신을 위해 폴더는 쓰기 가능해야 한다. Codex 앱의 전체 설정·플러그인·자료 폴더를 마운트하지 않는다.

compose.discord.codex.yaml은 Gemma 키 마운트 없이 사용할 대안 구성이다. DISCORD_TOKEN_PATH와 DISCORD_CODEX_HOME_PATH, 기존 전용 DB 구성은 필요하다. 운영 전환에는 아래 main 배포 명령을 이용한다. Compose 소스 빌드로 운영을 교체하지 않는다.

## main 배포에서 공급자 전환

PR CI 성공 → main 병합 → 해당 main CI 성공 후 실행한다. 기존 DB·네트워크·기록 볼륨은 보존되고, CLI 로그인 확인에 실패하면 기존 봇을 교체하지 않는다.

    python scripts/deploy_discord.py --provider codex_cli --codex-home 'C:\secrets\da-agent-codex'
    python scripts/deploy_discord.py --apply --provider codex_cli --codex-home 'C:\secrets\da-agent-codex'
    python scripts/deploy_discord.py --status

선택 모델·제한은 --codex-model과 --codex-timeout으로 지정할 수 있다. 공급자를 지정하지 않은 기존 --apply는 현재 운영 설정을 보존한다. 배포 실패 시 이전 이미지와 공급자 설정으로 복구한다. 배포 명령이 성공했더라도 실제 Discord 기능 확인은 별도다.

## 요청과 오류 동작

앱은 데이터·DB 비밀번호·Discord 토큰·API 키 환경을 CLI에 전달하지 않는다. 요청은 임시 폴더의 stdin으로 전달하고, exec에는 사용자 설정 제외·ephemeral·read-only·도구 비활성화를 적용한다. 외부 검색은 하지 않는다. 모델의 JSON 출력은 기존 Recipe·교육·평가 계약과 SQL 검산으로 다시 확인한다.

CLI에 API 키로 로그인한 경우에도 이 공급자는 호출을 거절한다. 로그인 필요·구독 한도·시간 초과·잘못된 JSON은 실패로 표시하며 부분 출력은 성공으로 저장하지 않는다. 출제 실패 요청은 /resume으로 상태를 보고, 원인 해결 후 /retry로 수동 재시도한다. 이미 전송한 요청이 시간 초과한 경우 구독 사용량이 발생할 수 있다.

추론 노력은 low로 요청한다. 복잡한 Recipe처럼 CLI의 strict 출력 스키마 규칙과 맞지 않는 계약은 원문 스키마를 입력에 유지하고 서버에서 검증한다. 호환되는 명시적 응답 스키마에는 --output-schema를 사용한다. 어댑터가 실패한 exec를 다시 실행하지는 않지만 CLI 내부 전송 복구 및 기존의 제한된 설계 수정·평가 보완은 별도 호출/사용량으로 발생할 수 있다.

실제 Discord 확인: 분석 연습 출제 → 자연어 조회 → 질문 → 보고·제출 → /resume에서 같은 과제와 평가 표시. 운영 전환 전 검증 결과와 운영 결과는 Spec에서 구분한다.

공식 근거: [인증](https://learn.chatgpt.com/docs/auth), [exec](https://learn.chatgpt.com/docs/non-interactive-mode), [명령](https://learn.chatgpt.com/docs/developer-commands).
