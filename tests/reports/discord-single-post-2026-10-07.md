# Discord 제출·평가 첫 포스트 통합 검증

- 날짜: 2026-10-07 (Asia/Seoul).
- 요청: `/submit` 후 평가 내용을 포럼 답글 대신 한 포스트에서 확인하기.
- 사용자 선택: 한 메시지 한도를 넘으면 첫 포스트에 핵심 내용과 전체 PDF를 첨부한다.
- 구조 확인: docs/scripts/specs/examples/src/template/tests가 모두 존재. 생성·이동 없음.

## 변경 내용

- `ResultForumPublisher.post_embeds()`가 요약과 제출·평가 섹션을 첫 메시지용 임베드로 구성한다. 짧은 카드가 10개를 넘더라도 섹션을 묶어 전체 내용을 표시한다.
- 전체 임베드의 제목·본문·작성자·footer·필드를 포함한 6,000자 및 임베드 10개 제한을 확인한다. 초과 시 항목별 일부와 명시적 PDF 안내를 표시한다. 긴 보고 조각은 미리보기에서 묶으며 평가 항목에도 공간을 배정한다.
- 새 글 생성과 재시도는 첫 메시지의 임베드·PDF·다운로드 버튼을 함께 작성/갱신한다. 평가 카드를 `thread.send()`로 답글에 게시하지 않는다.
- 기존 표식과 게시글 ID를 재사용한다. 이전 방식의 답글과 회원 댓글은 삭제·수정하지 않는다. 재시도에서 기존 첫 메시지를 갱신한다.
- PDF 내용, 저장 보고·평가·점수, 평가 호출 방식은 유지한다.
- 읽기 전용 운영 검증 스크립트를 첫 메시지의 실제 payload·PDF를 확인하는 방식으로 갱신했다. 과거 방식의 결과 답글 수는 별도로 기록한다.

## 자동 검증

```powershell
python -m pytest tests/automated/test_discord_results.py tests/automated/test_discord_pdf.py tests/automated/test_discord_transport.py tests/automated/test_discord_sql_practice.py -q
```

결과: **82개 통과 / 12개 생략**. 생략은 전용 PostgreSQL DSN이 필요한 기존 SQL 연습 검사다.

확인한 흐름: 첫 메시지 전체 표시, 10개 초과 섹션 묶기, 글자 수 경계·긴 결과·많은 섹션, 평가 보류, 긴 작성자 이름, 같은 글 재시도, 첫 메시지 갱신 실패 후 복구, 보관 글 재사용, 이전 표식 호환, 원문·평가 보존, PDF 생성·첨부 실패, PDF 누락 복구와 기존 첨부 보존, 생성 불확실성 중복 방지, 포럼 권한 오류, 모델 재평가 방지, 멘션 차단.

## 로컬 화면 확인

- [생성 스크립트](../../scripts/verification/preview_discord_single_post.py)는 실제 `post_embeds()`와 PDF 생성기를 사용한 고정 예시를 표시한다. Discord 운영 화면을 재현한 검증으로 간주하지 않는다.
- [미리보기](../artifacts/discord-single-post-2026-10-07/index.html): 브라우저에서 짧은 결과 전체, 긴 결과의 각 평가·후속 답변·연습 제안, 전체 PDF 안내를 확인했다. 짧은 결과는 임베드 2개/406자, 긴 결과는 2개/966자다.
- 긴 결과 링크 이동 및 새로고침 후 내용 유지 확인. [화면 증빙](../artifacts/discord-single-post-2026-10-07/preview-long.png).
- PDF 링크 클릭 확인. 앱 내부 브라우저는 PDF 본문을 표시하지 못했으므로 Chrome에서 동일 PDF의 한글 본문과 6페이지 문서 표시를 확인했다. [PDF 화면 증빙](../artifacts/discord-single-post-2026-10-07/pdf-chrome.png).

## 실제 Discord 확인

main 커밋 `18aa6962af96b0fc798f35d917b0ba2f8a9d17fb`으로 운영 배포하고 사용자가 선택한 별도 SQL 과제를 실제 Discord에서 생성·풀이·제출했다. 이전 본인 결과 글 두 개는 404를 반환해 검증 대상으로 재사용하지 않았다.

- 신규 과제: `b1eeb9d2-3a3a-4846-886b-0efddf0950b4`. 실제 SQL 실행 결과는 분모 40, 분자 30, 비율 0.75이며 평가 다섯 항목이 모두 충족됐다. SQL 평가는 항목별 결과이며 저장 total은 null이다.
- [실제 결과 글](https://discord.com/channels/1556888486919934064/1557217841730551810): 문제·제출 SQL·평가 등 11개 섹션이 첫 메시지의 임베드 2개에 모두 표시됐다. 본문 설명 합계 1,783자, 영구 평가 답글 0개.
- `/resume` 재시도 후 같은 글 ID·평가 ID·평가 1개·기록 지문·답글 0개 유지. 실제 REST 임베드 본문은 저장 결과에서 구성한 예상 내용과 일치했다.
- 결과 글을 새로고침한 뒤에도 요약·평가·PDF 버튼이 유지됐다.
- PDF 다운로드 버튼이 본인만 보이는 첨부를 제공했고 실제 파일을 다운로드했다. 3페이지 PDF에서 제출 SQL과 구문·계산·중복·기간·분모 평가 항목을 확인했다. 다운로드 파일의 직접 file URL 화면 열기는 브라우저 URL 정책이 거절해 실제 다운로드 PDF의 브라우저 시각 검증은 수행하지 않았다. 앞 절의 로컬 PDF 시각 검증과 실제 파일 내용 검증을 구분한다.
- [첫 메시지 화면](../artifacts/discord-main-release-2026-10-07/forum-first-post.png), [평가 부분](../artifacts/discord-main-release-2026-10-07/forum-evaluation.png), [다운로드 응답](../artifacts/discord-main-release-2026-10-07/pdf-download-response.png), [읽기 확인](../artifacts/discord-main-release-2026-10-07/forum-after-resume.json), [다운로드 PDF](../artifacts/discord-main-release-2026-10-07/downloaded-result.pdf).
- 한도 초과와 분석 평가의 새 운영 제출은 이번 SQL 화면 검증에서 수행하지 않았다. 해당 분기는 자동 검사·로컬 표시 검증 근거를 유지한다. 기존 방식의 답글과 회원 댓글은 자동 삭제하지 않는다.

한 메시지 한도 근거: [Discord 공식 메시지 문서](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx).
