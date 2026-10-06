# PR 충돌 해결 검증

- 날짜: 2026-10-06 (Asia/Seoul)
- 대상: PR #20 중단 응답 복구, PR #21 표·포럼·가독성. 기준 main: b93eb2e.
- PR #20: 최신 main을 병합, 응답 취소 전파·finish와 최신 질문 연결·표·포럼 게시를 함께 보존. 전체 pytest 411 passed, 65 skipped.
- PR #21: 해결한 PR #20을 병합하여 main 및 두 PR 사이의 충돌 제거. 최신 /query 새 조회·/answer 답변·/tip·간결한 안내 유지. 넓은 API 오류 분류와 답변 보존 유지.
- SDK 변경에 맞춰 표 테스트의 tables 키워드와 대기 질문·답장 fixture를 갱신. 모델에 누적 답변이 전달되는 조건을 확인.
- 전체 pytest 435 passed, 65 skipped. 선택 Discord/Pillow 의존성은 격리된 .local/pr-verification-deps에 설치. 실제 운영 DB·키·봇 변경 없음.
- 동일 이름 대화 기록: main의 dialog-3은 유지, PR #21의 원래 dialog-3은 dialog-5로 보존. 양쪽 사용자 판단 기록 모두 보존.
- 실제 Discord 화면 재검증은 수행하지 않음. 확인 행동: 봇 질문에 /answer로 답한 뒤 새 조회가 발생하지 않고 현재 질문을 이어가는지 확인. 중단 응답 표시 교체와 포럼 자동 생성 권한 검증의 기존 대기 상태 유지.
