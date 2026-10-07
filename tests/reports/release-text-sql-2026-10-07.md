# text 기반 SQL 출제 운영 반영 — 2026-10-07

## 요청과 범위

사용자는 SQL 자유 주제 출제 구현과 난이도별 리텐션 검증 뒤 운영 배포까지 요청했다. 기능 브랜치 → PR CI → main 병합 → main CI → 공식 배포 명령 → 실제 Discord 확인 순서를 적용한다.

최신 main의 분석 출제 복구 개선을 병합했다. SQL 품질 검사와 분석 품질 검사를 각각 유지하고, 의미 검토 실패의 초안·오류 보존 및 재설계를 통합했다. 기존 Spec 057과 번호가 겹쳐 이 기능 문서를 Spec 058로 변경했다. 과거 검증 산출물의 spec057 파일명은 이력을 보존한다.

## 반영 상태

| 구분 | 상태 | 근거 |
| --- | --- | --- |
| 로컬 통합 검증 | 완료 | 전체 자동 테스트 860 통과 / 89 환경 의존 생략; DB 회귀 160 통과 / 6 환경 의존 생략 |
| Git 반영 | 완료 | [PR #57](https://github.com/skh001218/DA-Agent/pull/57), PR/main 세 CI 성공, main 08494bac4393780a4c22c1c2f31e5e6b6d49d94f |
| 운영 배포 | 완료 | deploy_discord.py --apply --commit 08494bac…; Discord 연결·실행 파일 87개 인증; 이전 2fccc0b…에서 교체 |
| 실제 Discord 확인 | 완료 | 새 중급 D7 문제 생성·SQL 답장 실행·3/3 충족·결과 및 PDF 게시·resume 같은 결과 열람 |

초기 구현 검증과 난이도별 실제 모델 결과는 [구현 검증 보고](verification-text-sql-2026-10-07.md)에 기록했다. 비밀 구성과 배포 기록은 Git 제외 .local에 보존한다.

- [전체 자동 검사](../artifacts/spec058-release-2026-10-07/integrated-all.xml)
- [SQL·분석 복구 DB 회귀](../artifacts/spec058-release-2026-10-07/integrated-db.xml)

## 실제 운영 기능 확인

- `/training practice:sql`에 채널별 D7 리텐션과 무접속자 포함·관측 일수 7 미만 제외를 입력했다. 과제 `02b2a7c7-8b98-45c5-ae7e-7bce44529353`가 모델 설계·독립 검토 2회 후 준비됐다.
- 공개 스키마와 조건으로 직접 작성한 `COUNT(CASE WHEN … THEN 1 END)` SQL을 봇 입력 안내에 멘션 답장했다. 실행 `820006c7-c63f-4dc8-b426-c29e9d8c112d` 성공, 전체 결과 2행: paid/organic 각각 분모80·분자40·비율0.5. 이는 연습용 합성 자료의 값이다.
- `/submit`은 본 자료·표본 크기를 바꾼 두 자료·빈 데이터에서 일치했고 구문·계산·다른 표본 세 항목 모두 충족했다. 평가 `d3550c5c-a317-4ef5-a53b-32fe7add22a6`와 PDF가 포럼에 게시됐다.
- `/resume session_id:02b2a7c7-8b98-45c5-ae7e-7bce44529353` 후 같은 결과 글을 열고 카드·PDF를 확인했다. 평가1개와 출제 호출2회가 유지됐다. 과제 스레드는 보관·잠금 상태다.
- [운영 결과 글](https://discord.com/channels/1556888486919934064/1557314994562015275), [화면](../artifacts/spec058-release-2026-10-07/discord-result.png), [실행·평가 근거](../artifacts/spec058-release-2026-10-07/live-smoke.json).
- [PR CI](../artifacts/spec058-release-2026-10-07/pr-ci.json), [main CI](../artifacts/spec058-release-2026-10-07/main-ci.json), [운영 커밋·파일 인증](../artifacts/spec058-release-2026-10-07/runtime-status.json).

운영 실제 흐름은 중급 대표 과제에서 확인했다. 초급·중급·고급의 독립 SQL 검산과 실제 Codex 모델 출제 검증은 초기 구현 보고에 있다. 모델 출제 전체 성공률·교육 효과·사람 품질 승인을 이 결과로 주장하지 않는다. 이 후속 기록은 문서·검증 산출물만 변경하며 운영 코드 커밋은 위의 `08494ba`다.
