# Discord 분석 결과 PDF 실제 전체 흐름 검증

- 날짜: 2026-10-06 (Asia/Seoul), 실제 Chrome UI에서 16:24~16:35 수행.
- 요청: 처음부터 게시까지 테스트하고 사용자가 버튼을 누르면 PDF가 나오도록 준비.
- 서버: 개발테스트님의 서버 / da-agent / da-result.
- [과제 스레드](https://discord.com/channels/1556888486919934064/1556930261298585680)
- [PDF가 있는 포럼 게시글](https://discord.com/channels/1556888486919934064/1556931093222002710)

## 실제 수행 결과

| 순서 | 실제 행동과 결과 |
| --- | --- |
| 시작 | 부모 텍스트 채널에서 `/training`, 새로운 비공개 `DA-c60e3d9f-917` 생성과 업무 안내 확인 |
| 조회 | `/query`로 UTC 2026-09-08~09-22 이전 가입자, 10-01 이전 3단계 완료를 고유 사용자로 채널별 집계. 실제 PostgreSQL 실행 성공, 조회 ID `a5373a79-59c7-4baf-8777-045c83b2b8ef` |
| 결과 | ads 13/19=68.42%, organic 17/21=80.95%. 실제 표 첨부 표시 확인 |
| 가설/근거 | `/answer`로 가설·기각 조건 기록, `/evidence`로 성공 조회 선택 |
| 보고 | `/report`로 수치·분모/분자·중복 제거·합성 데이터 한계·추가 검증 계획을 포함한 검증용 보고 버전1 저장 |
| 후속 | `/followup`으로 담당자의 우선순위·지표·불확실성 질문에 답변, 저장 확인 |
| 평가/게시 | `/submit`, 실제 모델 평가 결과와 포럼 게시 확인. 점수100.0/100은 모델 판정이며 사람 검토는 pending |
| PDF 생성 | 첫 카드에 `analysis-991932e45d7b6909e47d.pdf` 첨부, 76,413bytes와 `PDF 다운로드` 버튼 확인 |
| 버튼/저장 | Chrome에서 버튼 클릭 → 본인만 볼 수 있는 개인 응답에 같은 PDF 제공. 그 응답의 파일 링크로 실제 Downloads 폴더에 저장 |
| 파일 확인 | 저장된 실제 PDF는3쪽, 제출 본문·수치·평가·후속 답변·조회ID 포함. 한글 TTF 내장. Poppler로3쪽 전체 렌더링 후 글자·여백·줄바꿈·페이지 번호 확인 |
| 재시작 | 봇 이미지 재생성·Gateway ready 이후 기존 게시글의 버튼에서 PDF 제공 확인 |
| 재개/중복 | 부모 채널 `/resume` 후 같은 과제 재개. 포럼 메시지10개 유지, PDF 첨부ID `1556931093536448543` 유지, 보고1개·평가1개·조회1개 유지, publication=published·error_code=null |

## 실행 환경 반영

기존 실행 프로젝트는 `C:/Users/Administrator/.codex/worktrees/discord-mvp/DA-Agent`이며 다른 후속 수정이 실행 이미지에 들어 있었다. 현재 실행 이미지의 나머지 코드를 그대로 보존한 overlay 이미지 `da-agent-discord-bot:pdf-20261006`에 PDF 관련 모듈, setup_hook의 영속 View 등록, ReportLab5.0.1과 NanumGothic 폰트를 반영했다. 기존 응답 복구·유지보수 로직도 보존했다.

로컬 재현 설정은 현재 worktree의 `.local/pdf-runtime-overlay/`에 있다. 기존 Compose 설정과 해당 `compose.pdf.yaml`을 함께 사용해 bot 서비스만 교체했고 DB·볼륨을 초기화하지 않았다. 임시 버튼 진단 출력은 최종 이미지에서 제거했다. 현재 봇은 running, restart count0이며 기존 토큰/키를 출력하거나 변경하지 않았다.

## 증거

- `tests/artifacts/discord-pdf-2026-10-06/downloaded-analysis.pdf`: 버튼 응답에서 실제 받은 PDF 사본.
- `downloaded-page-1.png`~`downloaded-page-3.png`: 실제 다운로드 파일의 화면 검증.
- `live-audit-before-resume.json`, `live-audit-after-resume.json`: 실제 Discord SDK/기록 DB의 읽기 전용 검증 요약. 영속 카드10개, 첨부ID 유지, 상태published와 평가 수1개 확인.
- 다운로드 원본: `C:/Users/Administrator/Downloads/analysis-991932e45d7b6909e47d (1).pdf`.

처음 버튼 자동 클릭에서 UI가 바뀌지 않아 브라우저 상태와 실제 버튼 hit target을 확인했다. 재로딩과 버튼 클릭 후 실제 Gateway 수신·개인 파일 제공 및 Chrome 응답을 확인했다. 단순히 버튼이 보이는 것으로 성공 판정하지 않았다.

다른 사용자 권한 차단은 기존 자동 테스트로 확인했으며 두 번째 사람 계정의 실제 UI는 이번 테스트에 포함하지 않았다. PDF 제공과 교육적 평가 품질의 사람 승인은 구분한다.
