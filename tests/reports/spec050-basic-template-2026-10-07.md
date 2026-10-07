# 기본형 문제 템플릿 검증 — 2026-10-07

## 구현과 자동 검증

- 사용자 선택: 생성 분석 문제에 기본형 적용. 업무 배경 → 해야 할 일 → 데이터와 조건 → 제출 내용 → 시작 안내.
- 관련 검사: `python -m pytest tests/automated/test_discord_basic_template.py tests/automated/test_discord_generation.py tests/automated/test_discord_readability.py tests/automated/test_discord_flow.py tests/automated/test_discord_sql_practice.py -q` — 40 passed, 16 skipped.
- 전체 검사: `python -m pytest tests/automated -q` — 706 passed, 83 skipped, 기존 Starlette/httpx 경고 1개.
- 새 검사 8개는 초급·중급·고급의 질문과 공개 조건, 명시된 판단 조건, 이전 저장 과제, 장문 전송과 멘션 차단, 생성 후 재개와 호출 수·저장 문서 불변을 확인한다. 난이도별 실제 모델 신규 출제 품질을 검증한 것은 아니다.
- 생성 그룹·정답 SQL·비공개 검산 지표는 안내에 포함하지 않는다. 구조화 공개 정보의 표시용 사본을 저장하며 기존 objective·평가 계약·지문 계산 대상은 보존한다.

## 로컬 화면 확인

실제 `task_intro` 렌더러와 `safe_chunks`의 출력으로 만든 로컬 HTML 미리보기를 Codex IAB에서 열었다. 중급·고급 표기 전환, 이전 문제 fallback, 긴 배경의 5개 메시지 분할, 내용 너비 375px에서 줄바꿈과 가로 넘침 없음, 새로고침 후 기본 화면을 확인했다. 배경, 번호가 붙은 질문 3개, 공개 데이터 단위와 기간 경계, 제출물과 명령이 모두 표시된다.

- 화면: [기본형 데스크톱 미리보기](../artifacts/spec050/basic-template-desktop.png).
- HTML: [로컬 출력 미리보기](../artifacts/spec050/basic-template-preview.html).
- 미리보기는 테스트 fixture의 합성 문제를 사용한다. 고급 버튼은 렌더러의 난이도 표기를 검사하며 고급 문제의 새 출제·품질 승인 검사가 아니다.

## 제한과 운영 확인 항목

DB·실제 모델·전용 생성 계정이 필요한 83개 검사는 로컬 설정이 없어 생략했다. 이번 요청은 템플릿 작성과 코드/Git 반영 범위이며 운영 배포·실제 Discord 게시를 수행하지 않았다.

운영 적용 후 기존 생성 문제의 스레드에서 `/resume`을 실행하여 구역 사이 빈 줄과 데이터 조건이 표시되는지 확인해야 한다. 구조화 항목이 없는 이전 문제는 전체 업무 요청을 배경에 보존한다. 새 출제는 공개 질문을 번호로 표시한다. 배포는 main 커밋 CI 성공 후 `python scripts/deploy_discord.py --apply`만 사용한다.
