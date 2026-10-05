# 훈련 준비 화면 단순화 검증

- 날짜: 2026-10-05 (Asia/Seoul)
- 브랜치: `codex/simplify-training-request`
- 시작 상태: detached HEAD와 main 모두 `5675f899c3b26baa201d9c4720cc26f9b06b6c49`, 변경 파일 없음. 해당 main 커밋에서 새 브랜치를 생성했다.
- 폴더 검사: docs, scripts, specs/examples, src, template, tests 모두 존재. 생성·이동 불필요.

## 확인 결과

- `node --check`로 app.js와 training.js 문법 검사 통과.
- `scripts/verification/verify_simple_training_browser.cjs`: 실제 Edge 브라우저 13개 검증 통과. 현재 작업 폴더의 HTML/JS/CSS를 브라우저 라우팅으로 제공하고 기존 8087 서버의 조회 API만 사용했다. 실행 중인 앱 파일은 교체하지 않았다.
- 제거된 DOM 없음, 빈 요청 안내, 원 요청문·난이도·SQL 수준·adaptive 기본값 전송, SQL 수준 미지정 시 생략, 추가 질문·새로고침 복구·실패·재시도·취소·입력 보존을 확인했다.
- 1280px·390px 스크린샷을 직접 확인했다. 모바일 가로 넘침 없음, pageerror 0건. 추천·지원 상태·패키지 목록 API 호출 없음.
- 기존 Docker 환경의 `/tmp`에 현재 src/tests를 복사해 `PYTHONPATH`를 지정한 비DB 회귀: **201 passed, 41 skipped**, 기존 라이브러리 deprecation 경고 1건.
- 최초 로컬 pytest는 sqlglot 누락으로 수집 실패했다. Docker 내 기존 앱 소스로 실행한 첫 회귀는 소스 버전 차이로 5건 실패했고, 현재 소스를 별도 경로로 제공한 최종 실행은 모두 통과했다.

## 한계와 재현

새 훈련 준비 응답은 모의 응답이다. 실제 AI 호출·새 DB 훈련 생성은 실행하지 않았다. 현재 동작 중인 8087 앱에는 변경을 배포하지 않았다. 해당 브랜치로 앱을 실행한 뒤 짧은 요청을 적고 ‘훈련 준비’를 눌러 실제 출제를 확인할 수 있다.

```powershell
$env:PLAYWRIGHT_MODULE = 'C:/Users/arusm/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
node scripts/verification/verify_simple_training_browser.cjs
```

[검증 JSON](../artifacts/simple-training-browser-2026-10-05/result.json), [데스크톱 화면](../artifacts/simple-training-browser-2026-10-05/home-1280.png), [모바일 화면](../artifacts/simple-training-browser-2026-10-05/home-390.png).

현재 adaptive 브라우저 스크립트 2개도 제거된 선택 항목 참조를 정리했다. 과거 v2 고정 데이터·추천 화면 검증 스크립트는 당시 검증 기록 재현용이며 이번 화면 검증은 위 스크립트를 사용한다.

## 후속 요청: 이어 하기 탭

같은 브랜치에서 새 훈련·이어 하기 탭을 추가했다. 기본 화면의 문제 목록을 숨기고 이어 하기 탭에서 표시한다. 기존 문제 열기 후 훈련 목록 버튼은 이어 하기 탭으로 돌아온다.

확장된 실제 브라우저 검증 **23개 통과**: 기존 13개에 기본 탭, 목록 표시 전환, 요청 입력 보존, 키보드 탭 전환, 선택한 탭 새로고침 유지, 데스크톱·390px 이어 하기 화면, 실제 저장 문제 열기, 분석 화면 탭의 독립 동작, 목록 복귀, 빈 목록 안내를 추가했다. 기존 훈련은 조회만 했고 저장·삭제·AI 호출은 실행하지 않았다. JavaScript 문법과 `git diff --check`도 통과했다.

최신 [첫 화면](../artifacts/simple-training-browser-2026-10-05/home-1280.png), [이어 하기 화면](../artifacts/simple-training-browser-2026-10-05/resume-1280.png), [모바일 이어 하기](../artifacts/simple-training-browser-2026-10-05/resume-390.png)를 직접 확인했다. 실행 중인 앱에는 아직 배포하지 않았다.

## 현재 앱 반영

후속 사용자 요청으로 `http://127.0.0.1:8087`의 `da-agent-app-1`에 화면 파일 네 개를 적용했다. 별도 main 작업 폴더는 여러 변경이 진행 중이라 수정하지 않았다. 컨테이너의 기존 training.js 코칭 객체 표시 수정을 3방향 병합으로 보존했다. 백업 위치: `C:/Users/arusm/AppData/Local/Temp/da-agent-ui-deploy-5997b47c-44b3-46dd-92ea-22bd6f91fc80/live-static`.

첫 병합에서 줄바꿈 차이 때문에 충돌한 index.html이 잠시 복사됐다. 원본을 즉시 복구하고 줄바꿈을 정규화해 재병합했다. 최종 파일 문법 검사 후 적용했고 `/api/health`는 정상이다. 서버 재시작이나 DB 변경 없이 정적 파일만 적용했다.

`scripts/verification/verify_simple_training_live.cjs`로 실제 배포 앱을 확인했다. 기본 새 훈련·제거 항목·탭 전환·입력 보존·탭 새로고침 유지·실제 기존 훈련 재개·보고서 탭·목록 복귀·1280px/390px 화면 확인 통과, pageerror 0건, 변경 API 요청 0건. [실제 앱 결과](../artifacts/simple-training-browser-2026-10-05/live-result.json)와 [현재 앱 화면](../artifacts/simple-training-browser-2026-10-05/live-home-1280.png)을 보존했다.

이번 반영은 현재 컨테이너의 실행 파일에 적용했다. 컨테이너를 재생성할 때도 유지하려면 이 브랜치의 UI 변경을 배포 소스에 통합해 빌드해야 한다. 다른 작업 중인 main 소스는 그대로 두었다.
