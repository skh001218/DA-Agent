# 출제 개선 통합과 운영 반영

- 날짜: 2026-10-07 (Asia/Seoul)
- 브랜치: codex/discord-generation-repair-20261007, 공통 기준 5d1d601.
- 사용자 요청: Git 반영 전 충돌·DB 호환·통합 검사·파일 누락 문제 해결. 앞서 요청한 운영 반영을 검증한 통합본으로 실행.
- 다른 621f 체크아웃은 수정하지 않았다. 그 작업의 코드·문서 변경을 현재 브랜치에 통합했다. 통합 전 원본과 문서 사본은 ignored `.local/pre-integration-20261007`에 보존했다.
- 표준 docs/scripts/specs/examples/src/template/tests 폴더가 모두 존재하며 구조 이동은 없다.

## 해결한 문제

| 문제 | 처리와 검증 |
| --- | --- |
| 출제·provider 충돌 | 최신 초안·오류별 수정·전송 직전 계수 예약과 운영 countTokens, 재시도 KST 시각, SQL·고급 판단 검사를 통합 |
| 예산 DB 호환 | 운영 pending 열과 완료 시각 갱신을 보존. INSERT 열 이름 명시, 4열 마이그레이션·5열 기존 사용량 보존·미전송 예약 해제·timeout 사용량 유지 검사 |
| 통합 검사4개 실패 | 필수 practice 선택, 토큰 계산과 generateContent 요청 구분, pending/완료 예약의 서로 다른 만료 계약을 검사. 생성 실패를 성공으로 표시하거나 validator를 완화하지 않음 |
| Spec·대화 파일 충돌 | Spec036은 최신 배포 진행도와 개선 내용을 함께 보존. 양쪽 대화를 dialog-1.md와 dialog-2.md로 보존. 기존 검증 보고는 원문 운영 자료의 위치를 식별하는 사본으로 정리 |
| 필수 새 모듈 누락 | 임시 `.local` 통합본 대신 실제 src/tests로 반영. discord_design.py, discord_api_budget.py와 회귀 테스트를 Git 포함 목록에서 확인 |

운영의 최대130초 예산 대기는 유지하되 그 동안 추론 호출을 전송하거나 일일 사용량을 차감하지 않는다. HTTP429에 대한 generateContent 자동 재시도는 하지 않는다. 토큰 계산 실패는 보수적인 UTF-8 추정치로 처리한다. 완료율의 분자는 분모 모집단의 부분집합이라는 운영의 기존 계산 계약도 별도 가상 출제 프롬프트에 보존했다.

## 검증

- 전체 초기 통합: 627 passed / 80 skipped.
- 격리 PostgreSQL을 포함한 최종 전체: **657 passed / 52 skipped**, [JUnit](generation-integration-all-2026-10-07.xml). 과거 재시도 시각 잔존 표시 수정 후 재검사한 결과다.
- 마지막 분모 프롬프트 보존 후 관련 검사: **70 passed / 1 skipped**. 이 생략은 해당 실행에 records DSN을 제공하지 않은 검사이며 앞선 전체 DB 실행에서 통과했다.
- 생략52개는 웹/기타 전용 DB·별도 실행 옵션이 필요한 기존 검사다. 이 숫자를 실행 완료로 계산하지 않는다. 기존 Starlette/httpx 사용 경고1개.
- 추가 회귀는 정확한 토큰 계산에 따른 미전송·미차감, 기존 pending 볼륨의 사용량 보존, 응답 timeout 후 예약 완료, KST 안내, 고급 judgment 계약, 제한된 예산 대기 뒤 차감 순서를 확인한다.
- 격리 DB fixture: 생성→FK 조회→근거·보고→제출 보류→서비스 재생성 복원→읽기 계정 쓰기 차단 확인. [결과](../artifacts/generation-integration-2026-10-07/integration.json).
- 화면: 빈 요청 거절, 중급 fixture 생성, 실제 플랫폼별 평균2행 조회, 보고 저장, 새로고침 후 동일 훈련·조회1개·보고1개·후속 질문 복원. [화면](../artifacts/generation-integration-screen-2026-10-07.png). 실제 모델 품질과 구분한다.

## 운영 반영

- 현재 이미지: `da-agent-discord-bot:generation-integrated-20261007`.
- 롤백 이미지: `da-agent-discord-bot:rollback-before-generation-integration-20261007`.
- 현재 배포 overlay: `.local/deploy-generation-integration-20261007/compose.deploy.yaml`.
- 기존 env 파일·비밀 파일·Discord DB 및 generation/responses/quality 볼륨을 재사용했다. 운영 DB와 웹 앱 컨테이너는 재시작하지 않았다.
- 반영 전 진행 중인 생성0개. 반영 후 `Discord ready`, 컨테이너 running, 재시작 횟수0.
- 현재 src와 실행 컨테이너의 주요10개 파일을 정규화한 SHA256으로 비교해 모두 일치했다. 임의 버전 표지만 확인한 결과가 아니다.

```powershell
docker compose --env-file C:/Users/Administrator/.codex/worktrees/discord-mvp/DA-Agent/.local/discord-runtime/compose.env -p da-agent-discord -f compose.discord.yaml -f .local/deploy-generation-integration-20261007/compose.deploy.yaml up -d --no-deps --no-build bot
```

코드 재배포는 같은 인수로 build bot을 먼저 실행한다. 이전621f overlay로 재배포하면 과거 이미지로 돌아갈 수 있으므로 현재 설정을 사용한다. 원격 복원 시 ignored overlay는 기존 운영 env 경로를 사용해 재구성해야 한다. 비밀 파일은 Git에 올리지 않는다.

## 실제 모델 재시도

Discord 스레드1557195802575904788의 출제 재시도 버튼을 한 번 실행했다. 기존 요청과 기록을 유지한다. 첫 응답은 완료됐고 countTokens에 여유256을 더한 입력 예산4027이었다. judgment의 accepted_limit/decision_rule 일부가20자 미만이어서 서버가 거절하고 최신 초안으로 수정 요청을 진행했다. 두 수정 응답의 입력 예산은6178,6153이었다. 세 응답 모두 완료됐고 이번 시도에서API429는 없었다. 마지막 파생 표가 앞선 source/group_by/groups 제약을 충족하지 않아 plan_invalid로 끝났다. 요청의 누적 추론 호출 수는 기존1회+이번3회=4회다. [원문 없는 진단 수치](../artifacts/generation-integration-2026-10-07/live-summary.json).

실제 화면에서 새 설계 오류에도 과거 API 재시도 시각이 남는 것을 발견했다. 재시도 시작 시 이전 시각을 제거하고 현재 실패가api_rate_limited일 때만 시각을 표시하도록 추가 수정 및 회귀 검사했다. 기존 기록의 오래된 필드도 잘못 표시하지 않는다. 완성된 분석 문제는 아직 없으며 자동 검사·봇 접속·API429 없는 응답을 출제 성공으로 표시하지 않는다. Spec036의 실제 분석 전체 흐름은 계속 미완료다.

시각 안내 수정은 재검사 후 같은 통합 이미지 태그로 다시 빌드·반영했다. 실제 Discord에서 `/resume`을 한 번 실행해 같은 스레드와 새 실패 안내를 복원했다. 새 안내에는 과거 API 시각이 없으며 출제 호출 수는4로 유지됐다. 운영 화면은 ignored `.local/deploy-generation-integration-20261007/discord-resume.png`에 보존했다. 최종 운영 소스10개 일치와 봇 접속도 재확인했다.

## Git 반영 준비

검사 시점 후보40개 약1.29MB에서 충돌 표식·UTF-8 대체 문자·100MB 초과 파일·현재 문서 링크 누락은0건이었다. 운영에 마운트된 실제 API 키와 Discord 토큰2개의 값과 후보 텍스트를 비교해 일치0건, 토큰·개인키 형식도0건이었다. 값은 출력·저장하지 않았다. 모든 종류의 민감정보 검사를 보증하는 결과는 아니다.

필수 새 모듈과 테스트를 포함한 파일을 Git index에 추가했고 staged diff --check를 확인했다. `.local` 및 생성 packages는 제외되고 운영 세션·초안 원문은 커밋 후보에 넣지 않았다. 양쪽 원본 작업을 별도 반영하기보다 이 통합 브랜치를 기준으로 올려야 중복 병합 충돌을 피할 수 있다. 이후621f 쪽에 변경이 생기면 다시 차이를 확인한다. 이전 실패 검사 기록은 [사전 점검](git-readiness-2026-10-07.md)에 보존했다. 커밋·원격 푸시는 하지 않았다.
