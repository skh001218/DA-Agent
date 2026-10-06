# Discord Gemma 실제 API 후속 검증

- 날짜: 2026-10-06 (Asia/Seoul)
- 브랜치: codex/discord-mvp
- 모델: gemma-4-26b-a4b-it (Google Gemini API를 통한 Gemma)
- 결과: 실제 연결·분석 표본 검증 완료. Discord 서버·사람 검토는 대기.

## 실제 연결과 격리

기존 서버 비밀 파일을 **읽기 전용**으로 일회용 컨테이너에 마운트했다. 키를 출력·복사·커밋하지 않았다. Models API에서 이 키에 허용된 gemma-4-26b-a4b-it/31b-it를 확인하고 26b 모델로 generateContent를 실제 실행했다. 웹 GEMINI_MODEL과 기존 api_provider.py에는 변경하지 않았다. 봇은 DISCORD_MODEL과 DiscordGemmaProvider를 사용한다. 운영 웹 재배포/재시작·운영 DB 변경을 하지 않았고 최종 /api/health가 ok였다.

da-discord-gemma-test 네트워크와 da-discord-gemma-db postgres17.4에서 discord_test/discord_records_test DB, 별도 learner 역할로 실행했다. 합성 공개 과제만 모델에 전달했다. 실제 Discord 토큰·테스트 서버/채널 설정은 확인되지 않아 로그인·명령 등록·서버 메시지 전송은 수행하지 않았다.

## 자동 회귀

최종 현재 코드 전체: **364 passed, 44 skipped, 1 warning**. 이 44개는 운영 DB를 사용하지 않기 위해 별도 회귀 환경에서 실행했다. 해당 별도 전체 실행은 **375 passed, 19 skipped, 2 warnings**였으며 원래 웹/DB44개는 모두 통과했다. 두 실행의 공통 테스트를 합산하지 않는다. 별도 실행의 Discord DB19개는 현재 코드 실행에서 전부 수행했다.

- [기존 DB 회귀 기록](verification-discord-db-followup-2026-10-06.md)
- [JUnit 증거](discord-db-verification-2026-10-06.xml)
- 새 자동 검증: API JSON 정규화/명시 모델8개, step 정규화/충돌2개, 최고 등급/하위 개선 문구/등급 고정 보완4개.

## 실제 Gemma 표본

최종 **14/14개 표본**이 해당 기대 조건을 충족했다. 초기 실패 → 코드 보완 → 최신 결과를 연결해 판정했다. 초기 실패 기록을 삭제하거나 대체 API 결과로 덮지 않았다.

| 표본 | 확인 내용 |
| --- | --- |
| 모호한 비율 | SQL 전에 요청 의미 확인 |
| 채널별 가입자 | 실제 조회와 독립 SQL 결과 일치 |
| 단계3 도전 이벤트 | 단위/step 구분, 실제 도전 이벤트50건 집계 |
| 신규 코호트 완료율 | DISTINCT 분자/가입자 분모, 독립 SQL 결과 일치 |
| 정확한 D7 | D7 조건 해석과 실제 데이터 실행 |
| 주간 재방문 | 첫 기간20명 중 재방문16명,80% 실제 결과 |
| 없는 매출 데이터 | unsupported_query로 실행 차단 (429를 성공으로 계산하지 않음) |
| 타당한 불확실성 | 판정 보류/후속 검증을 오류로 취급하지 않음; 수정 후 실호출 |
| 가설 수정 | 초기 가설을 유일 원인으로 확정하지 않는 행동 인정; 수정 후 실호출 |
| 인과 핵심 오류 | metric_design/evidence_interpretation의 공개 오류 판정 |
| 시스템 실패 | 확정 점수 대신 보류/total null |
| 최종 제출·복원 | 실제 조회→근거→보고→후속 답변→실제 평가 완료, 영구 근거 유지 |
| 확인 대화 영구 저장 | 모호한 원문→사용자 확정 답변→실제 조회, 원문/답변/조건 복원 |
| 동일 사실의 다른 문체 | 두 실모델 응답의 항목 등급 동일; 파서 보완 후 **기존 실제 응답** 재검사 |

[최종 표본별 근거 매핑](../artifacts/discord-gemma-final-summary-2026-10-06.json)에 각 테스트가 근거로 사용하는 파일·표본 이름을 기록했다. 문체 검증은 실응답 재검사이며 새 API 재평가를 했다고 주장하지 않는다. 최종 제출은 보완 후 실제 API를 다시 호출했다.

## 실제 발견과 수정

1. Gemma가 JSON을 Markdown 블록으로 반환했다. 전체 JSON 블록만 정규화하고 임의 설명에서 JSON을 추출하지 않는다.
2. 초기 실검증은 API429로 차단됐다. 요청 간격/한도와 한정 재시도를 적용했다. 첫 검증기의 unsupported 상태만으로 성공을 세던 조건을 reason=unsupported_query로 수정했으며 초기 파일의 잘못된 성공 표시는 바로잡아 보존했다.
3. 모델이 period_basis/unit을 누락하거나 step을 filters 안에 배치했다. 완전한 조건 예시를 제공하고 **동등한 step 조건만** 정규화했다. 충돌은 확인 질문으로 보류한다.
4. 최고 등급인데 빈 improvement를 반환하여 기존 계약 검사가 평가 전체를 보류했다. 최고 등급은 시스템이 제공한 다음 연습 안내를 표시하고, 하위 등급은 구체적인 개선 문구가 필요하다. 원래 등급/이유를 변경하지 않는다.
5. 하위 등급의 누락 문구·근거 ID는 **등급을 고정한 1회 보완**만 허용한다. grade 변경·허용 밖 ID·재실패는 보류한다. 사용량·일별 한도는 보완 호출도 포함한다.

## 재현 스크립트

검증용 DSN 환경을 전용 테스트 DB에만 설정하고 읽기 전용 키 파일을 넘긴다. 실제 모델을 호출하므로 일반 pytest에 자동 포함하지 않는다.

- verify_discord_gemma.py: 지표·오류·평가 행렬, 최대20회 호출과 기본16초 간격.
- verify_discord_gemma_followups.py: 확인 답변·영구 저장·문체·최종 제출.
- recheck_discord_gemma_evaluation.py: 기록된 실제 응답을 동일 파서로 검사한 후 실제 제출 재시도. --positive-only는 보류/가설 수정의 실제 재호출.

API 원시 응답을 저장한 followups 파일도 합성 사용자·공개 과제만 포함한다. 토큰·헤더·키는 포함하지 않는다. 비용은 단가를 확정하지 않아 null이며 0원으로 표시하지 않는다.

## 남은 확인

실제 Discord의 두 사용자·비공개 권한·버튼/명령·삭제/복구·Intent·Interaction 만료는 테스트 서버·토큰 정보가 필요하다. 첫 확인 행동은 별도 환경에서 `/training topic:tutorial difficulty:intermediate`를 실행해 전용 스레드의 업무 요청·사전·공개 평가 기준을 확인하는 것이다.

실모델 표본은 코드·응답 계약 검증이다. 대표 과제의 교육적 타당성, 배점/등급 적절성, 같은 결함 중복 감점 여부, 성장 비교 가능성은 사람이 검토해야 한다. 특히 실모델이 높은 등급을 준 보고가 상위 조건을 충분히 입증했는지도 검토한다. human_review와 comparability_review는 pending을 유지하며 교육 효과·출시 완료를 주장하지 않는다.
