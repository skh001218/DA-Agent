# /question 용어 설명 검증 — 2026-10-06

## 확인한 동작

- 실제 Discord 명령 목록에서 `/question text` 선택 및 입력 성공.
- `/question text:ads와 organic이 뭐야?`: ads 2줄, organic 3줄 표시. 모델 호출 0회.
- `/question text:ROAS가 뭐야?`: 광고비 대비 매출의 뜻·숫자 예시·주의점을 3줄로 표시. 모델 호출 1회.
- 같은 과제의 직전 저장 응답과 비교해 state, pending_question, pending_query, queries, executions, selected_evidence, reports, evaluations, conditions가 그대로 유지됨을 확인.
- 화면 확인 과제: `c60e3d9f-9179-490d-8956-5a3eac24bfb3` (완료 상태의 기존 테스트 과제).
- 모의 저장소의 실제 서비스 검증에서는 대기 질문 중 용어 질문 이후 `/answer`가 기존 요청·질문을 이어받는 것까지 확인.

## 자동 검증

최종 전체 테스트: 457 passed, 65 skipped. 용어 전용 테스트: 22 passed. 생략 항목은 전용 DB 또는 실제 모델 설정이 필요한 기존 테스트이며 새 용어 테스트는 모두 실행됐다. 기존 httpx 사용에 관한 Starlette 경고 1개가 있었다.

기본 용어·한국어 별칭·대소문자·중복·단어 경계·복합 용어·3줄 제한·긴 질문·과다 용어·모델 오류·불완전 JSON·호출 기록·완료/중단 상태·소유자 검사·이벤트 재처리를 검증했다.

## 증거와 실행

- [실제 화면](../artifacts/discord-term-question-2026-10-06.jpg)
- [DB 비교 결과](../artifacts/discord-term-question-2026-10-06.json)
- 읽기 전용 검증 스크립트: `scripts/verification/verify_discord_terms.py`
- 로컬 검증: `python -m pytest tests/automated/test_discord_terms.py -q`

운영 컨테이너에는 기존 코드와의 차이를 보존한 채 이번 기능 변경만 적용했다. 변경 전 파일은 Git 제외 경로 `.local/work/term-runtime/`에 보존했다. 컨테이너 재생성 시 이번 체크아웃의 기능을 포함한 이미지로 다시 빌드해야 한다.

명시적 줄 기준으로 3줄을 제한하며 화면의 자동 줄바꿈 수는 기기 폭에 따라 달라진다. 모델이 설명하는 모든 미등록 용어의 의미적 정확성이나 악의적 질문에 대한 의미적 응답 품질까지 검증한 것은 아니다.
