# PRD-v2 구현 및 통합 검증 (2026-10-04)

## 최종 결과

- Spec 008~013의 백엔드·화면 코드를 통합했다. 독립 모듈은 새 관리 워크트리 3개에서 구현했고 공통 계약/API/생성/추천은 순차 연결했다.
- 표준 폴더 docs/scripts/specs/examples/src/template/tests는 모두 존재했다. 기존 파일 이동과 DB 볼륨 초기화는 하지 않았다.
- Windows AMD64 / Docker Linux x86_64 / Python 3.12.10 / PostgreSQL 17.4에서 최종 실제 DB 회귀 **196개 통과, 건너뜀 0개**. 호스트 검사 167개 통과, DB·symlink 조건 28개 건너뜀(후속 지원 밖 DB 회귀 1개 추가).
- [최종 실행 JSON](verification-v2-release-2026-10-04.json)에 소스 해시·host/container 일치·버전·환경·실행 요약을 기록했다. raw traceback, 접속 문자열, 키, 임시 SQL/결과/대화 원문은 기록하지 않았다.
- `docker compose build app prepare` 후 app을 재생성했다. 기존 고정 패키지 manifest 지문 검사와 JavaScript 문법 검사도 통과했다.

## 실제 확인한 범위

| 범위 | 근거 |
| --- | --- |
| 요청·고정 계획 | v1/v2 분리, 네 유형, 부족/충돌/지원 밖/SQL 자료 차단, 추가 질문과 동일 ID/다른 내용 충돌 |
| 생성·검증·공개 | 전용 generator 역할, 50~1000명·10000세션 상한, PK/FK/시간·사전·해시·독립 집계와 실제 SQL; 학습자 미공개 검증 |
| 실패·재시도 | 검증 실패에 attempt 없음/learner 권한 없음, 같은 seed·데이터 해시·plan revision으로 수동 재시도 성공, 시작 우회 차단 |
| 정리 안전성 | 24시간, 참조/활성 요청/삭제 직전 참조 재확인, identity/경로/스키마 검사, 생성한 독립 실제 스키마만 정리 |
| 학습 상태·판정·로그 | revision·이견·선호·출처 삭제 무효화, 판정 버전/이력, 실패/중복/중단 작업, 원문 비수집, 30일 보관 |
| 근거·평가 | 고정 평가 hash·양의 배점 합100, 설계 SQL 제외, 완전한 실제 저장 집계/비교, 타 훈련/잘린 결과/허위 수치 차단 |
| 추천 | 명시 선택·선호 우선, 미관측/잠정 유지, 이견/무효 근거 제외, 최근5 의미 비교, seed/제목/숫자 무시, 의도적 반복, 안정 동률, draft 기존 데이터 fallback |
| 지표·화면 | 8종 분모/결측/기간/버전 집계와 JSON/CSV; 실제 브라우저 저장/삭제 취소/다운로드/390px/콘솔 |

DB 회귀에서 LLM 제공자는 계약 검증용 fixture다. 아래 실제 모델 실행과 구분한다. 테스트는 별도 `verify_request_<uuid>` recorder 스키마와 임시 패키지·스스로 만든 학습 스키마만 정리한다.

## 실제 모델·브라우저와 품질 결과

[실제 모델 흐름](verification-v2-live-2026-10-04.json): Gemini gemini-3.5-flash-lite의 요청 ready → 실제 SQL → 선택 저장 → 보고서 → 리뷰 completed → 저장 재개 → 해당 검증 기록 삭제를 확인했다. [실제 화면 기록](verification-v2-ui-2026-10-04.md), [Chrome 결과](browser-v2/result.json)의 실제 확인 항목을 따른다.

| 반복 평가 | 결과 |
| --- | --- |
| [초기 18회](verification-v2-quality-live-2026-10-04.json) | 16회 완료·2회 API 호출 제한; 불확실성 점수 편차25 |
| [지침 보완](verification-v2-quality-live-2026-10-04-rerun.json) | 불확실성 편차0; 마지막2회 호출 제한 유지 |
| [5초 호출 간격](verification-v2-quality-live-2026-10-04-paced.json) | 18회 모두 완료; 근거 부족 편차25 |
| [최종 지침](verification-v2-quality-live-2026-10-04-final.json) | 18회 모두 완료·전 회차 자동 항목 판정 pass; 5개 표본 편차0, 핵심 오류 표본12.5로 편차 기준10 초과 |

따라서 최종 verdict=fail, semantic_approval=false, human_quality=pending을 보존한다. 점수를 조정하거나 실패 회차를 제외하여 통과 처리하지 않았다. 프롬프트가 지침을 따르는 것과 의미적 오진/미탐/대안 거부/비공개 노출 판정은 별개다. 코칭 6종·전 유형/난이도 반복과 사람 판정이 남는다.

[규칙 검토 표본](verification-v2-rule-samples-2026-10-04.json): 네 규칙 각각 초급·중급·고급 총12개를 실제 DB로 검증하여 저장했다. 표본의 집계·비교/검토 SQL도 학습자 권한 없이 검증한다. 운영자는 공개 과제·사전·행 수·미리보기를 읽고 승인해야 한다. 신규 생성은 draft를 유지하며 이번 작업에서 승인 버튼을 대신 누르지 않았다.

## 재현

```powershell
python scripts/verify_v2_release.py --docker da-agent-app-1 --tests /tmp/da-tests-final --output tests/verification-v2-release-2026-10-04.json
```

컨테이너 tests 경로에는 최신 `tests/.`를 복사한다. 소스는 compose build로 반영한다. 전용 생성 암호는 `GENERATOR_DSN` 또는 `GENERATOR_PASSWORD_FILE` secret으로 읽으며 원문을 출력하지 않는다.

다른 PC·ARM과 실제 학습자5명 파일럿은 미수행이다. Spec 013의 출시·학습 효과 완료로 표시하지 않았다. 개별 진행도는 Spec 008~013 상단과 PRD 2절을 따른다.
