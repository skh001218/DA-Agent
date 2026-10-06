# Discord 분석 결과 PDF 검증

후속 요청에서 실제 실행 봇과 Chrome을 찾아 시작→게시→버튼 PDF 제공·파일 저장·재시작·재개 검증을 완료했다. 아래 환경 제약과 실제 확인 대기는 최초 검증 당시 기록이며, 최신 결과는 [실제 전체 흐름 검증](discord-pdf-live-verification-2026-10-06.md)을 따른다.

- 확인일: 2026-10-06 (Asia/Seoul)
- 구조: docs, scripts, specs/examples, src, template, tests 존재. 생성·이동할 표준 폴더 없음.
- 실행: `python -m pytest`에 `tests/automated/test_discord*.py`의 실제 경로 목록 전달. **136 passed, 19 skipped**. 생략된 테스트는 DB/실제 모델 등 별도 환경이 필요한 기존 검증이며 새 PDF 테스트는 모두 통과했다.
- PDF 회귀: 한글·긴 보고서 700개 반복 내용 보존, 특수문자, 평가 보류, 공개 정보만 포함, 원본 불변, TTF 내장, 안전한 파일명 확인.
- 게시 회귀: 첨부 권한·생성 오류·업로드 용량 오류에서 글 생성/완료 기록 없음; 업로드 403 후 재시도 성공; 반복 게시에서 PDF 재생성 없음; 첨부 없는 기존 글에 다른 첨부를 보존하며 보완; 기존 부분 게시 및 legacy 카드 복구 확인.
- 버튼 회귀: 영속 View와 setup_hook 등록, 현재 메시지 재조회·첨부 전달, 사용자별 응답, 서버·채널 접근·작성자 검증, 첨부 삭제와 HTTP 오류 안내 확인.
- PDF 화면: `PYTHONPATH=src python scripts/verification/verify_discord_pdf.py`로 QA PDF 생성. Poppler `pdftoppm -r 85 -png`로 7쪽 전체 렌더링 후 확인. 한글·본문 줄바꿈·여백·섹션 제목·페이지 번호·마지막 줄이 표시되며 잘림이나 겹침 없음. 산출물은 `tests/artifacts/discord-pdf-2026-10-06/`.
- 코드: `git diff --check` 통과.

## 실제 화면 확인 대기

이 작업 공간에는 `.local/discord-runtime/bot.env`와 Discord 테스트 서버 연결이 없다. 운영 봇 배포와 실제 Discord 게시·버튼 클릭·파일 저장을 수행하지 않았다. 모의 Discord 객체 테스트를 실제 화면 확인으로 계산하지 않는다.

1. Discord 봇 실행 환경에서 `requirements-discord.txt`를 설치하거나 새 Docker 이미지를 빌드한다. Linux는 NanumGothic TTF, Windows는 맑은 고딕이 필요하며 사용자 지정 TTF는 `DISCORD_PDF_FONT`로 지정한다.
2. 테스트 서버 DA-Result 포럼에서 봇의 파일 첨부 권한을 허용하고 저장된 과제에서 `/submit`을 실행한다.
3. 첫 카드의 **PDF 다운로드**를 눌러 개인 응답의 파일을 저장하고 열어 보고서와 평가를 확인한다. 원래 카드의 파일 첨부도 직접 저장 가능하다.
4. 봇 재시작 후 같은 버튼을 다시 누르고, `/resume` 후 게시글과 PDF 첨부가 중복되지 않는지 확인한다.
5. 포럼 접근 권한이 없는 사용자는 내려받을 수 없는지 확인한다.

## 참고

영속 View는 timeout=None과 고정 custom_id로 등록하며 시작 때 add_view를 호출한다. [discord.py 공식 예제](https://github.com/Rapptz/discord.py/blob/master/examples/views/persistent.py)를 확인했다. 다운로드는 메시지의 현재 첨부를 다시 가져오고 `Attachment.to_file()`로 제공한다. [discord.py 공식 API](https://discordpy.readthedocs.io/en/stable/api.html#discord.Attachment.to_file)를 참고한다.
