# Spec055 — 포럼 핵심 요약 운영 적용 검증

- 확인일: 2026-10-07 (Asia/Seoul)
- 요청: 테스트한 핵심 요약 템플릿을 운영에 적용한다.
- Git 반영: [PR #53](https://github.com/skh001218/DA-Agent/pull/53) 병합 완료. main `3f900512cc3096f11c7a58661b17f97aea2de789`.
- 운영 반영: 해당 main 커밋 배포·Discord 연결·실행 파일 지문 확인 완료.
- 실제 Discord: 기존 분석 결과 재개, 신규 SQL 실행·제출·재개, 양쪽 PDF 버튼 다운로드 확인 완료.

## 구현과 검증

- 저장된 구조화 필드·라벨 있는 보고서와 라벨 없는 수치/비교 문장에서 발췌한다. 추가 모델 호출·새 점수·새 근거를 생성하지 않는다.
- 실제 최신 분석 보고서의 첫 문장이 `전체 흐름 검증용 보고서`인 경우를 발견했다. 자유 문장에서는 저장된 비교 문장 `커뮤니티가 10%p 높지만 이것을 채널의 인과 효과로 확정할 수 없다`를 `제출 발췌`로 표시하도록 검증했다.
- 최신 저장 분석의 평가 보류와 SQL의 `30/40 = 75.00%`, `5/5개 충족`을 확인했다. 읽기 전용 DB 조회만 수행했다.
- 기존 결과 PDF 카드·저장 평가·보고서·근거 입력을 변경하지 않는다. 기존 첫 메시지와 PDF 버튼/첨부를 재사용한다.
- 보류는 점수를 숨기며 공급자/문제 유형 검증 실패를 학습자 감점으로 바꾸지 않는다. 계산 오류는 건수·수정 행동을 표시한다.
- 본문 900자 이하, 근거 3개 이하, 임베드 1개와 UTF-8 전송 예산을 검증했다.
- 관련 테스트: `test_discord_result_summary.py`, `test_discord_results.py`, `test_discord_pdf.py` → **58 passed**.
- 전체 회귀: `python -m pytest -q` → **786 passed / 83 skipped**. 생략은 별도 DB 등 환경 전용 검사다. GitHub의 전용 DB CI를 별도로 확인한다.
- 실제 브라우저: 분석/SQL/보류/계산 오류 전환, 새로고침, SQL PDF 다운로드를 확인했다. 생성한 PDF는 초기 확인본의 전체 PDF를 그대로 복사하여 원문을 보존했다.

| 운영용 발췌 사례 | 본문 길이 | 임베드 |
| --- | ---: | ---: |
| 공개 분석 표본 | 348자 | 1 |
| 공개 SQL 표본 | 269자 | 1 |
| 파생 평가 보류 | 285자 | 1 |
| 파생 계산 오류 | 359자 | 1 |

확인본·JSON·스크린샷: [운영용 렌더러 화면](../artifacts/spec055-compact-results-2026-10-07/index.html).
초기 수동 선택 시안의 301/356자와 운영 자동 발췌의 길이는 다르다. 초기 시안 검증은 당시 커밋 `264f29e`의 기존 게시 렌더러와 비교한 기록이며, 운영용 자동 발췌 검증과 구분한다.

## Git·운영·실제 확인

1. 기능 PR #53 head `b5f051db670285da5d2208c4a5ba9b68d1fb4f76`의 `automated-tests`, `discord-db-tests`, `discord-image` 성공 후 병합했다. main `3f900512cc3096f11c7a58661b17f97aea2de789`에서도 세 CI 성공을 확인했다.
2. `python scripts/deploy_discord.py --apply --commit 3f900512cc3096f11c7a58661b17f97aea2de789` 성공. `--status`에서 running=true, source_ref=refs/heads/main, files_verified=85 확인. 이미지 ID: `sha256:c5d720d7cfa98dd65144b64abed0ec03c254738e99ec19d9cebbf82cef820170`.
3. 분석 세션 `35cb724d-3dcf-488f-9f86-575418692926`을 실제 Discord에서 `/resume`했다. [같은 결과 글](https://discord.com/channels/1556888486919934064/1557286559252877375)의 임베드가 2개에서 1개·366자로 변경됐다. 평가 보류와 인과 해석 한계가 표시됐다. 평가 수·평가 해시·보고서 해시·모델 호출 기록·사용량·첨부 ID·PDF SHA는 변경 전과 같다. 중복 결과 답글은 없다.
4. 분석 PDF 버튼 응답에서 내려받은 파일은 252,435바이트·9페이지이고 원래 첨부와 SHA256이 같다.
5. 기존 SQL 표본의 포럼 글이 외부에서 사라져 있어 새 초급 SQL 과제로 확인했다. 최초 일반 검증 문구는 지원 지표 안내로 거절됐고, 지원되는 신규 가입자 3단계 완료율 요청으로 정상 생성됐다. 공개 과제의 가입 기간 `2026-09-08 이상 ~ 2026-09-22 미만`, UTC 관측 종료 `2026-10-01 미만`에 맞춘 SQL을 답장 제출했다. `/submit` 후 [신규 SQL 결과](https://discord.com/channels/1556888486919934064/1557296006608191529)에 1개·232자 카드, `30/40 = 75.00%`, `5/5개 충족`이 표시됐다. 결과 답글은 없고 완료 과제는 보관·잠금 상태다.
6. SQL PDF 버튼 응답 다운로드는 101,787바이트·3페이지이며 첨부 PDF SHA와 일치한다. 전체 SELECT가 포함돼 있다. 세션 `da8aaf4b-a38f-4be2-b666-868f058c830a`을 `/resume`한 뒤에도 같은 글·평가 1개·평가/보고서/모델 호출/사용량 해시·첨부 ID·PDF SHA·중복 답글 없음이 유지됐다.

운영 증거: [분석 화면](../artifacts/spec055-compact-results-2026-10-07/discord-analysis.png), [분석 PDF 버튼](../artifacts/spec055-compact-results-2026-10-07/discord-pdf-button.png), [SQL 화면](../artifacts/spec055-compact-results-2026-10-07/discord-sql.png), [SQL PDF 버튼](../artifacts/spec055-compact-results-2026-10-07/discord-sql-pdf-button.png), [분석 불변성 검사](../artifacts/spec055-compact-results-2026-10-07/live-analysis-checks.json), [SQL 재개 검사](../artifacts/spec055-compact-results-2026-10-07/live-sql-checks.json).

검증 기록의 후속 문서 반영은 실행 코드를 바꾸지 않는다. 이번 기능의 배포·화면 검증 기준은 위의 `3f90051` main 커밋이다. 이후 동시에 진행된 Spec056 PDF 개선이 main에 병합·배포됐으며, 최종 상태 조회에서 두 기능을 포함한 `2fccc0bb851f4d4e3385dacd5277752d7b6c12fd`, running=true, files_verified=86을 확인했다. Spec056의 3쪽 학습형 PDF 검증은 해당 작업의 보고서에서 관리한다.

기존 포럼 전체를 일괄 변경하지 않으며 새 제출과 기존 결과의 `/resume`에 적용한다. 비공개 구성과 원시 운영 스냅샷은 `.local`에 두고 Git에 추가하지 않는다.
