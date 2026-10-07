# Spec 048 — Codex 구독 CLI로 Discord AI 연결

## 구현 진행도

- 진행도: 7/7개 완료 (100%)
- 마지막 갱신일: 2026-10-07
- 남은 작업: 없음 (연결 기능 기준. 평가 품질 승인·반복 검증은 별도 운영 작업)
- 차단 사유: 없음

| 작업 | 상태 | 완료 조건 | 관련 코드 / 검증 결과 |
| --- | --- | --- | --- |
| CLI 공급자 | 완료 | ChatGPT 인증 확인, 격리된 exec, 완료 이벤트·JSON 검증, 시간 초과·오류 처리 | codex_provider.py, 실제 JSON probe와 경계 테스트 통과 |
| Discord 연결 | 완료 | 선택한 공급자로 출제·조회 해석·교육·평가를 수행, 기존 검증과 호출 한도 보존 | DiscordSettings·공급자 factory, 전용 DB 회귀 58개 통과 |
| 실행 환경·안내 | 완료 | 고정 CLI 버전과 컨테이너 인증 볼륨, Gemma 키 없이 설정 가능, 재현 가능한 안내 | Docker 이미지 CLI 0.160.0 실행·지문 확인, codex-cli-setup.md |
| 자동 검증 | 완료 | 프로세스 경계·오류·설정·기존 Discord 회귀 검사 통과 | 전체 687개 통과·80개 조건부 건너뜀, CLI/배포 51개·게시/PDF 45개 통과 |
| 실제 호출·핵심 흐름 | 완료 | 실제 구독 호출과 출제·조회·교육·평가·실패 복구 확인 | 임시 DB에서 실제 출제·조회·용어 설명·평가 검증 passed·재개 확인. 실패한 첫 출제는 보존·거절 확인 |
| main 배포·Discord 확인 | 완료 | PR·main CI 성공, main 배포 후 실제 Discord 핵심 흐름 확인 | PR #36·main 7f91f63 CI와 main 배포 성공. 실제 출제·조회 2개·용어 설명·평가 passed·저장 평가 재개 확인. 포럼 게시 실패는 아래 별도 작업으로 기록 |
| 포럼 게시·PDF 복구 | 완료 | 한글 전송 크기 제한·빈 스레드 복구, main 배포 후 같은 저장 평가의 포럼/PDF 확인 | PR #37·main c13aa04 CI/배포 성공. 실제 resume으로 published·오류 해제, 평가 1개·호출 기록 8개 보존. PDF 버튼/첨부 다운로드와 7쪽 PDF의 보고 원문 확인. 게시/PDF 45개 테스트 통과 |

작업 범위 갱신: 프로젝트 운영 절차에 따른 main 배포·화면 확인을 별도 항목으로 추가해 5개에서 6개로 재산정했다. 새 공급자의 평가 품질 프로필은 미승인이다. 피드백·검산을 보존하고 총점 보류를 유지하며, 단일 smoke 검증으로 품질 승인을 대신하지 않는다.

운영 화면에서 실제 게시 실패를 발견해 포럼 게시·PDF 복구를 일곱 번째 작업으로 추가했다. 기존 평가·과제 데이터는 유지하고, 모델을 다시 호출하지 않는 저장 결과의 게시 복구를 검증한다.

## 목적과 결정

사용자는 Gemma API 대신 Codex CLI를 선택했다. 이유는 기존 구독을 활용해 API 비용을 줄이기 위해서다. Discord 전용 연결을 구현하며 웹의 공급자 설정은 유지한다.

## 요구사항

- DISCORD_LLM_PROVIDER=codex_cli로 명시적으로 선택한다. 생략하면 기존 Gemma 연결이다.
- 공식 codex exec를 이용한다. codex login status에서 ChatGPT 로그인이 확인된 경우만 호출한다. API 키·access token·workload identity 인증 환경을 자식 프로세스에 전달하지 않는다. 앱에서 토큰 파일을 파싱하거나 비공개 HTTP API를 호출하지 않는다.
- 입력은 명령 인자 대신 표준 입력으로 전달하고 shell을 사용하지 않는다. 요청별 임시 작업 폴더, read-only sandbox, approval_policy=never, ephemeral 세션, 사용자 설정 제외를 적용한다. shell·MCP/플러그인·앱·웹 검색·하위 에이전트 도구를 비활성화한다. 사용자 입력은 자료로 전달한다.
- 가상 문제 생성(synthetic)을 사용한다. 다른 모델·Gemma·API 키로 자동 대체하지 않으며 어댑터는 실패한 exec를 자동 재실행하지 않는다. CLI 자체의 전송 복구와 기존의 제한된 출제 수정·평가 보완 호출은 별도이며 구독 사용량이 발생할 수 있다.
- turn.completed, 정상 종료, 최종 agent message의 단일 JSON 객체가 모두 확인되어야 완료다. 실패 이벤트·불완전 출력·잘못된 JSON·도구 실행은 실패한다. 기존 Recipe·질문·평가 검증과 독립 SQL 검산이 최종 판단을 담당한다.
- 시간·입력·출력 크기 제한과 공급자 인스턴스의 호출 직렬화를 둔다. 시간 초과 시 프로세스 트리를 종료한다. 취소·앱 호출 예산 검사는 before_send 계약으로 실행 직전에 적용한다. 이미 전송한 요청은 사용량이 발생할 수 있다.
- CLI 미설치·로그인 필요·구독 한도·모델 접근·시간 초과를 구분해 한국어로 안내한다. 원문 stdout/stderr·인증정보·입력은 공개 오류나 기록에 넣지 않는다. 사용량 이벤트가 있으면 토큰 수만 저장한다.
- DISCORD_CODEX_BIN, DISCORD_CODEX_HOME, DISCORD_CODEX_MODEL, DISCORD_CODEX_TIMEOUT_SECONDS를 지원한다. 모델 생략 시 CLI 기본 모델을 사용한다. DISCORD_MODEL의 Gemma 값은 Codex에 전달하지 않는다.
- CLI 추론 노력은 medium으로 지정한다. 2026-10-07 사용자의 middle(중간) 요청을 Codex 공식 설정값 medium으로 반영했다.
- 컨테이너에는 고정 CLI 버전과 갱신 가능한 전용 인증 볼륨이 필요하다. 공개 CI는 로그인하지 않는다. 운영 활성화는 main·CI·배포 절차를 따른다. 로컬 구현 검증과 운영 Discord 검증은 구분한다.

## 완료 기준과 검증

1. 가짜 CLI 실행기로 stdout/stderr·종료 코드·시간 초과·사용량·JSON·인증 환경 격리·예약 호출·설정을 검증한다.
2. 기존 Discord 출제·조회·교육·평가·전송 회귀 검사를 실행한다.
3. 실제 ChatGPT 로그인 CLI로 짧은 JSON 응답을 받은 뒤 격리 DB에서 핵심 훈련 흐름을 검증한다.
4. 운영 전환 시 실제 Discord /training → query/question → report/submit → resume를 확인한다. 미확인 항목은 완료로 표시하지 않는다.

## 공식 근거

- [Codex 인증](https://learn.chatgpt.com/docs/auth): ChatGPT 구독 로그인, forced_login_method.
- [비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode): exec, stdin, JSONL 완료 이벤트, ephemeral, output-schema.
- [CLI 명령](https://learn.chatgpt.com/docs/developer-commands), [설정](https://learn.chatgpt.com/docs/config-file/config-reference): sandbox·도구·사용자 설정 제어.
