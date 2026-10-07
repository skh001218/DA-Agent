# Spec048 Codex CLI 검증 — 2026-10-07

## 구현·로컬 검증

- CLI: 공식 Codex CLI 0.160.0, ChatGPT 로그인. API 키·비공개 backend HTTP 호출 없이 실제 JSON probe 성공. low 추론 노력·도구 비활성화 설정으로 확인.
- 전체 자동 검사: 684 passed, 80 skipped. 환경 조건이 있는 DB·외부 검사는 건너뛴 결과와 구분한다.
- CLI·배포 경계 검사: 51 passed. API 키/로그인 미완료 차단, 환경 격리, stdin, 정상 완료 이벤트, 잘못된 JSON, 시간 초과·프로세스 종료, 출력 제한, 예약 호출 실패, preflight 실패 시 기존 운영 미교체를 확인.
- 별도 임시 PostgreSQL에서 Discord 전용 회귀: 58 passed. 운영 DB·웹 DB를 사용하지 않았다.
- 개발 검증 이미지의 CLI 버전과 release 지문 검사 통과. 로컬 미커밋 이미지는 운영에 사용하지 않는다.
- 임시 DB의 실제 Codex 흐름: 플랫폼별 일일 활동량 과제 출제 2회(설계·독립 검토), SQL 조회 성공·실제 결과 저장, 선택 편향 용어 설명 성공, 평가 quality_validation=passed, 같은 과제·평가 재개 확인.
- 신규 공급자의 평가 품질 프로필은 pending이다. 피드백·검산 결과는 보존하고 total=null·held=true를 유지했다. 반복 품질 검증·사람 승인은 이번 smoke 검사로 완료 처리하지 않는다.
- 실패 흐름: 최초 튜토리얼 완료율 설계의 관측 기간 검산이 부족하다는 독립 검토 후 수정안이 plan_invalid로 거절됐다. 실패 요청과 진단은 보존됐으며 대체 문제를 그 요청의 성공으로 저장하지 않았다.
- CLI strict 스키마의 모든 필드 required 규칙과 기존 Recipe 계약이 맞지 않음을 실제 확인했다. 호환되는 응답 스키마에만 output-schema를 적용하고, Recipe 원문 스키마·서버 검증은 유지했다.

재현 도구: scripts/verification/verify_codex_cli.py, scripts/verification/verify_codex_discord_flow.py. 실제 호출에는 --live가 필요하며 구독 사용량을 소비한다. 원문 로컬 증거는 Git에 올리지 않는 .local/spec048에 보관한다.

## Git 반영

구현 [PR #36](https://github.com/skh001218/DA-Agent/pull/36)의 세 CI 검사 성공 후 main 7f91f63a377f9e2a3b3ddbeab6a68f474a2d3f17에 병합했다. 해당 main의 automated-tests·discord-db-tests·discord-image도 모두 성공했다.

게시 복구 [PR #37](https://github.com/skh001218/DA-Agent/pull/37)도 세 CI 성공 후 main c13aa04c1e25b756baf6d2391479e0e53ae9f838에 병합했고 해당 main CI도 모두 성공했다.

## 운영 반영

지정된 deploy_discord.py --apply --provider codex_cli 명령으로 main 7f91f63을 배포했다. 상태 조회에서 refs/heads/main·codex_cli·실행 중·82개 파일 지문 일치를 확인했다. Discord ready와 별도 인증 preflight도 통과했다. 운영 이미지 ID는 sha256:8f65925fe5599e371285db3220a467ffd827170c98a60f9a8f35fde39752bd53이다. 기존 DB·네트워크·기록 볼륨은 배포 절차로 보존했다.

전용 인증 저장소는 Git 밖의 C:\Users\Administrator\.codex\.local\da-agent-discord-codex다. 기존 공식 CLI의 auth.json만 복사했으며 사용자 설정·플러그인 폴더는 공유하지 않았다. 관리자·SYSTEM 권한으로 제한하고 토큰 갱신이 가능한 쓰기 마운트를 사용했다. 인증값은 코드·이미지·기록에 넣지 않았다.

## 실제 Discord 화면 확인

실제 Chrome의 개발테스트 서버에서 /training을 실행했다. 첫 중급 요청 950ddc20-ac6a-40a1-b522-41a987d299ba는 세 CLI 응답을 받은 뒤 Recipe 규칙(계정키 고유성·진단 하위집합)에서 거절됐고 unsupported_scope·실패 기록·수동 재시도 안내가 보존됐다. 이 요청은 성공으로 표시하지 않았다.

별도 초급 요청 cf2616e8-a763-4783-83e9-53b35496ebb8는 모바일·PC 평균 플레이 횟수 과제 생성과 자동 검증을 통과해 비공개 스레드에 표시됐다. /query의 복수 집계 요청은 한 집계 선택을 묻는 확인 질문으로 연결됐고 /answer로 평균 조회를 선택했다. 평균 조회(모바일 8.41·PC 13.55)와 계정 수(각 100)가 실제 SQL 성공 기록·Discord 표로 표시됐다. 두 실행을 /evidence로 연결하고 /question의 선택 편향 설명, /report·/followup·/submit를 확인했다. 평가 quality_validation=passed, provider_failure 없음, held=true·total=null이다. /resume은 같은 저장 피드백을 다시 표시했고 추가 모델 호출이 없었다.

화면에서 발견한 공급자 고정 안내(Gemma가 설계·API 호출 한도)는 선택한 모델·모델 호출 한도 표현으로 수정했다. 이 문구 변경 후 관련 회귀는 107 passed·14 skipped다.

## 실제 게시 오류와 보완 검증

최종 평가의 포럼 게시가 HTTP 413·code 40005로 거절됐다. Discord 응답은 message.embeds=10150·message.components=52를 초과 필드로 제시했다. 라이브러리의 문자 수는 4679로 6000자 이내였지만 한글 직렬화 전송 크기에서 거절됐다. 빈 포럼 스레드가 남아 /resume 시 첫 메시지 조회에서 404·10008 Unknown Message가 재현됐다. 이는 CLI 응답 실패와 구분되는 결과 전송 실패다.

보완: 문자 제한과 함께 직렬화 UTF-8 본문에 보수적인 6000바이트 한도를 적용한다. 크기를 초과하면 요약·항목 미리보기를 줄이고 원문은 PDF에 그대로 유지한다. 첫 메시지가 없는 후보 스레드는 제목만으로 연결하지 않는다. creating/uncertain 상태의 중복 생성 금지는 유지한다. 한글 크기·원문 보존·실패/불확실 생성 복구 테스트를 추가했으며 게시/PDF 검사 45개 통과했다. 이 보완의 PR·main 배포·실제 확인은 아래 복구 완료 절에 기록한다.

보완 후 전체 자동 검사는 687 passed·80 skipped이며 기존 httpx 관련 경고 1개다.

공식 문서의 6000자 제한과 이 환경에서 관측된 전송 거절을 구분한다. 바이트 한도는 앱의 보수적인 운영 가드이며 Discord의 보편적인 공식 제한이라고 주장하지 않는다. [Discord 공식 Embed 제한](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx).

## 게시 복구 완료 — 2026-10-07 12:37 KST

- main c13aa04c1e25b756baf6d2391479e0e53ae9f838의 CI 성공 후 deploy_discord.py --apply로 배포했다. 상태: refs/heads/main, codex_cli, 실행 중, 82개 파일 지문 일치. 이미지 sha256:2f24105aa44deef39ef1616470a0f0142b552caf60a8194f220a9ab13720b709.
- 실제 Chrome 부모 채널의 /resume session_id:cf2616e8-a763-4783-83e9-53b35496ebb8로 같은 평가의 게시를 복구했다. 상태 published, error_code=null, 평가 1개·호출 기록 8개 유지. 추가 모델 호출이나 재평가 없이 복구됐다.
- 동일 결과의 본문은 임베드 2개·2501자·직렬화 UTF-8 5636바이트로 제한 안에 들어갔다.
- [실제 결과 게시글](https://discord.com/channels/1556888486919934064/1557235411242328078)의 첫 메시지에서 요약·점수 보류·항목 미리보기와 전체 PDF 안내를 확인했다. PDF 다운로드 버튼을 누른 뒤 본인에게 표시된 첨부 응답도 확인했다.
- 첨부 analysis-c7b1fba14a366fe2c5fe.pdf 실제 다운로드: 169052바이트, 7쪽. PDF 텍스트에서 저장 보고 원문 전체와 과제 ID·8.41·13.55·5.14 및 마지막 평가 항목을 확인했다. 원문을 미리보기로 대체하지 않았다.
- 실제 화면 증거는 Git 제외 .local/spec048/discord-result-top.jpg 및 discord-result.jpg, 저장 흐름은 runtime-flow-beginner.json에 보관한다. 다운로드 PDF는 사용자 Downloads 폴더에 있다.
- 일회용 로컬 DB 컨테이너 da-agent-spec048-db는 검증 후 정리했다. 운영 DB·웹 DB·기존 기록은 보존했다.
- CLI 연결 기능은 완료다. 평가 프로필은 미승인이고 총점은 보류 상태다. 새로운 모델의 반복 품질 검증과 사람 승인은 완료로 기록하지 않는다.
