# 코칭 응답 템플릿 적용 검증

- 기록일: 2026-10-05 (Asia/Seoul)
- 대상: request-v2 코칭. 출력 계약 coaching-v2 유지, 프롬프트 cumulative-coach-v3.
- AI 응답은 action_type, reason, next_action, evidence_ids, uncertainty 다섯 항목이 모두 필수다. evidence_state는 서버가 검증한 출처들의 상태로 계산해 화면용 응답에 추가한다.
- Gemini 요청에 responseMimeType=application/json 및 responseJsonSchema를 적용한다. 허용 필드·행동·실제 출처 ID 목록을 제한하며 서버에서 길이·중복·출처·행동·비공개 노출을 다시 검증한다. 다른 공급자는 같은 템플릿과 서버 검증을 사용한다.
- JSON 해석 실패(coaching_json), 필드 실패(coaching_schema), 출처 실패(coaching_source), 행동 실패(coaching_action), 맥락 실패(coaching_context), 비공개 노출(answer_exposure)을 구분한다. 모델 원문이나 사용자 자료를 오류에 복사하지 않는다.
- 템플릿 예시: [coaching-response.json](../template/coaching-response.json). 예시의 ID·문장을 현재 맥락에 맞게 작성하도록 명시했다.

## 검증 근거

- 실제 Chrome·앱·Gemini: [화면 결과](prd-v2-browser-2026-10-05/coaching-template.json), [화면 캡처](prd-v2-browser-2026-10-05/coaching-template.png). 일반 코칭 정상 응답, 저장 대화 새로고침 복원, 객체 응답의 읽을 수 있는 표시, 임시 응답의 새로고침 후 소멸을 확인했다. 화면 오류 0개.
- 실제 반복 검증: [6종×3회 결과](verification-coaching-template-2026-10-05.json). 기존 대표 계산 과제의 고정 표본을 사용하며 실패를 제외하지 않는다. 사람 판정은 미수행이다.
- 최종 실행 b5cc1197-701d-4219-905d-958d8811fb34: **18/18회 completed, 형식·출처·행동 값 검증 실패 0회**. 기대 행동 자동 판정은 16/18회 통과했다. 가설 충돌 표본 2회가 기대 suggest_analysis 대신 submit을 선택해 전체 verdict는 fail이며 semantic_approval=false를 유지한다. 제출 행동 빈칸 오류는 최종 3회 모두 해소했다. 형식 문제 해결과 의미적 행동 품질 미완료를 구분한다.
- 최초 responseFormat 방식은 현재 실제 API에서 18회 모두 api_request_invalid로 거부했다. [초기 기록](verification-coaching-template-api-rejected-2026-10-05.json)을 보존하고 호환 설정으로 수정했다. API 호출 자동 재시도는 추가하지 않았다.
- 호환 설정 적용 후 첫 18회에서는 15회 통과, 근거 충분 표본 3회는 submit인데 next_action이 빈 문자열인 coaching_action 실패였다. [중간 기록](verification-coaching-template-submit-empty-2026-10-05.json)을 보존했다. 별도 진단 호출로 submit/빈 행동을 확인하고, submit에는 현재 결론·한계 정리 및 보고서 제출 행동을 반드시 작성하도록 명시했다. 자동 검증 조건을 완화하지 않았다.
- 최초 구현 DB 회귀: [244개 통과](verification-coaching-template-db-2026-10-05.json). API 호환 설정·대화 표시·프롬프트 버전 기록까지 반영한 최종 회귀는 [최종 기록](verification-coaching-template-final-db-2026-10-05.json)을 따른다.

## 남은 검증

가설 충돌에서 분석 제안보다 제출을 앞세우는 행동 판단을 추가 개선·재검증해야 한다. 대표 계산 표본 이외의 과제·수준에서도 반복 검증하고, 사용자 또는 별도 검토자가 이유·다음 행동의 유용성, 대안 인정, 비공개 정보의 의미적 노출 여부를 확인해야 한다. 자동 검사 통과는 사람 품질 승인이 아니다. 평가 점수 편차와 출제 AI 해석 오류는 이번 코칭 변경 범위 밖이며 기존 검증 대기를 유지한다. 테이블 생성 한도와 AI 호출 한도·자동 재시도 정책은 변경하지 않았다.
