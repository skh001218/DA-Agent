# SQL 작업 탭과 문법 강조 편집기

## 구현 진행도

- 진행도: 3/3개 완료 (100%)
- 마지막 갱신일: 2026-10-05
- 남은 작업: 본 기능 완료 조건 없음
- 차단 사유: 없음

| 작업 | 상태 | 완료 조건 | 관련 코드 / 검증 결과 |
| --- | --- | --- | --- |
| SQL 작업 탭 | 완료 | 분석 탭에서 SQL 작업대·사전을 옮기고 한 탭에서 함께 사용 | index.html, styles.css; 실제 소스/배포 화면 확인 |
| SQL 문법 강조 | 완료 | PostgreSQL 키워드·문자열·숫자·주석 구분, 입력·실행·초기화 연결 | sql-editor.js, app.js; 네 가지 색상·줄 번호·입력 원문·되돌리기·fallback 실행 확인 |
| 실제 동작 검증 | 완료 | 탭 전환·입력 보존·SQL 실행·선택 저장·오류·새로고침·모바일 검증 | verify_sql_workspace_browser.cjs; 실제 DB 12개 검증, 소스/현재 앱 모두 통과 |

## 목적과 사용자 선택

사용자는 데이터 분석 연습에서 SQL 작성과 데이터 사전을 분석 화면에서 분리하고, SQL 문법이 눈에 구별되는 입력을 요청했다. 추가 확인에서 데이터 사전과 편집기를 하나의 ‘SQL 작업’ 탭에 함께 배치하는 구성을 선택했다.

## 동작과 경계

- 분석하기 → SQL 작업 → 보고서 작성 → 제출·리뷰 순서로 탭을 제공한다.
- 분석 탭은 업무 요청, 힌트, 생각 정리와 코칭을 제공한다. SQL 작업 탭은 데이터 사전, SQL 편집기, 실행 결과를 함께 제공한다.
- SQL 입력은 PostgreSQL 문법 색상과 줄 번호를 제공하고, 탭 전환 시 입력과 실행 결과를 유지한다.
- 편집기의 입력 원문으로 기존 SQL 실행·선택 저장을 사용한다. 새 훈련을 열면 편집기 입력과 실행 이력을 초기화한다.
- SQL 편집기의 원문과 미저장 결과는 기존 정책대로 새로고침 이후 복원하지 않는다. 저장한 SQL 근거는 복원한다.
- 편집기 파일은 로컬 정적 자산으로 제공하며 외부 CDN이나 CSP 완화를 사용하지 않는다. 편집기 로딩 실패 시 기존 textarea를 사용한다.
- 파일럿 단계 기록에서는 SQL 탭을 기존 analysis 단계로 기록한다. 서버의 기존 단계 계약을 유지한다.

## 편집기 의존성

CodeMirror 5.65.21과 SQL 모드(MIT)를 로컬로 포함한다. 정적 CSS를 사용하는 편집기로 기존 `style-src 'self'` 정책을 유지한다. 배포 자산은 static 최상위 파일로 제공해 Python 패키지의 기존 static/* 포함 규칙을 따른다. 사용 근거: [공식 매뉴얼](https://codemirror.net/5/doc/manual.html), [공식 SQL 모드](https://codemirror.net/5/mode/sql/index.html).

의존성 재현: 임시 디렉터리에서 `npm install --prefix <임시경로> codemirror@5.65.21 --ignore-scripts --no-audit --no-fund`로 고정 버전을 받는다. `lib/codemirror.js`, `lib/codemirror.css`, `mode/sql/sql.js`, `LICENSE`를 각각 static/codemirror.js, codemirror.css, codemirror-sql.js, codemirror-LICENSE.txt로 포함했다. 실행 시 npm이나 인터넷 연결은 필요하지 않다.

## 검증과 현재 앱 반영

[검증 기록](../tests/reports/verification-sql-workspace-2026-10-05.md)에 실제 화면·DB 결과를 기록했다. 현재 8087 앱에 반영했으며 기존 코칭 객체 표시 변경은 병합해 보존했다. 진행 중인 main 소스는 변경하지 않았다. 임시 SQL·미저장 결과는 새로고침하면 사라지는 기존 정책을 유지한다.
