# Spec 번호 정리 검증 기록

- 정리일: 2026-10-07 (Asia/Seoul)
- 정리 전: 문서 47개, 중복 번호 그룹 8개.
- 정리 후: 활성 문서 46개, 001~046 연속 번호. 다음 번호: 047.
- 정렬 기준: 기존 번호 순서, 중복 그룹 안에서는 선행 기능 → 후속 기능. 서로 다른 기능은 모두 유지.
- 동일 사본: `020-chatgpt-login-probe.md`는 `019-chatgpt-login-probe.md`와 바이트 단위 동일. [보관 사본](../../specs/archive/chatgpt-login-probe-duplicate.md)에 원문을 유지.
- README·PRD·Spec·검증 기록·대화 기록의 정확한 파일 경로를 갱신. 현재 문서의 Spec 번호와 링크 표시 번호도 함께 갱신.
- 과거 검증 보고의 본문 번호, 검증 스크립트·테스트·산출물 파일명은 실행 당시 식별자로 유지. 기존 구현 진행도와 검증 수치는 유지.

## 이전 번호와 현재 문서

| 이전 파일명 | 현재 문서 |
| --- | --- |
| `019-source-backed-task-generation.md` | [020-source-backed-task-generation.md](../../specs/020-source-backed-task-generation.md) |
| `019-discord-owned-records-and-lifecycle.md` | [021-discord-owned-records-and-lifecycle.md](../../specs/021-discord-owned-records-and-lifecycle.md) |
| `020-discord-natural-language-query.md` | [022-discord-natural-language-query.md](../../specs/022-discord-natural-language-query.md) |
| `021-discord-education-evaluation.md` | [023-discord-education-evaluation.md](../../specs/023-discord-education-evaluation.md) |
| `022-discord-bot-transport.md` | [024-discord-bot-transport.md](../../specs/024-discord-bot-transport.md) |
| `023-discord-integration-and-release-validation.md` | [025-discord-integration-and-release-validation.md](../../specs/025-discord-integration-and-release-validation.md) |
| `024-discord-gemma-api-validation.md` | [026-discord-gemma-api-validation.md](../../specs/026-discord-gemma-api-validation.md) |
| `025-discord-isolated-runtime.md` | [027-discord-isolated-runtime.md](../../specs/027-discord-isolated-runtime.md) |
| `026-discord-supported-topic-guidance.md` | [028-discord-supported-topic-guidance.md](../../specs/028-discord-supported-topic-guidance.md) |
| `027-discord-thread-members-access.md` | [029-discord-thread-members-access.md](../../specs/029-discord-thread-members-access.md) |
| `028-discord-readable-task.md` | [030-discord-readable-task.md](../../specs/030-discord-readable-task.md) |
| `028-discord-concise-task-intro.md` | [031-discord-concise-task-intro.md](../../specs/031-discord-concise-task-intro.md) |
| `029-discord-conversation-recovery.md` | [032-discord-conversation-recovery.md](../../specs/032-discord-conversation-recovery.md) |
| `029-discord-question-replies.md` | [033-discord-question-replies.md](../../specs/033-discord-question-replies.md) |
| `030-discord-table-images.md` | [034-discord-table-images.md](../../specs/034-discord-table-images.md) |
| `030-discord-command-tips.md` | [035-discord-command-tips.md](../../specs/035-discord-command-tips.md) |
| `031-discord-interrupted-response.md` | [036-discord-interrupted-response.md](../../specs/036-discord-interrupted-response.md) |
| `031-discord-results-forum.md` | [037-discord-results-forum.md](../../specs/037-discord-results-forum.md) |
| `031-discord-result-pdf.md` | [038-discord-result-pdf.md](../../specs/038-discord-result-pdf.md) |
| `032-discord-term-question.md` | [039-discord-term-question.md](../../specs/039-discord-term-question.md) |
| `032-discord-evaluation-verification-and-resubmission.md` | [040-discord-evaluation-verification-and-resubmission.md](../../specs/040-discord-evaluation-verification-and-resubmission.md) |
| `033-discord-completed-thread-cleanup.md` | [041-discord-completed-thread-cleanup.md](../../specs/041-discord-completed-thread-cleanup.md) |
| `033-trustworthy-evaluation-quality-loop.md` | [042-trustworthy-evaluation-quality-loop.md](../../specs/042-trustworthy-evaluation-quality-loop.md) |
| `034-discord-archived-training-and-titles.md` | [043-discord-archived-training-and-titles.md](../../specs/043-discord-archived-training-and-titles.md) |
| `035-discord-sql-practice-mode.md` | [044-discord-sql-practice-mode.md](../../specs/044-discord-sql-practice-mode.md) |
| `036-discord-text-task-generation.md` | [045-discord-text-task-generation.md](../../specs/045-discord-text-task-generation.md) |
| `037-discord-bounded-generation-repair.md` | [046-discord-bounded-generation-repair.md](../../specs/046-discord-bounded-generation-repair.md) |
| `020-chatgpt-login-probe.md` | [019-chatgpt-login-probe.md](../../specs/019-chatgpt-login-probe.md) (동일 사본 별도 보관) |

## 검증 결과

- 활성 Spec 46개: 번호 중복 0건, 누락 0건, 001~046 연속 번호 확인.
- 문서 링크 188개: 대상 파일 존재 확인. 번호를 표시한 링크 81개: 표시 번호와 대상 번호 일치.
- 기존 Spec 46개의 진행도·마지막 갱신일 및 작업 상태 204개 보존 확인. 각 Spec의 본문 줄 수 유지.
- 보관한 로그인 사본은 정리 전 작업 공간 원본의 SHA-256과 동일.
- 기능 소스·자동 테스트·배포 설정 변경 없음. 문서 번호와 경로 검증으로 확인.
