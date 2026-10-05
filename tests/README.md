# 테스트와 검증 자료

- `automated/`: pytest가 실행하는 `test_*.py`. 기본 실행은 프로젝트 루트에서 `.venv/Scripts/python -m pytest -q`.
- `reports/`: 검증 결과 설명과 수동 검증 절차. 새 검증 보고서도 여기에 저장한다.
- `artifacts/`: 검증 JSON, 화면 이미지, 다운로드 자료. 한 검증의 결과는 같은 기능·회차 폴더에 모은다.

검증 실행 도구는 `scripts/verification/`, 조사 도구는 `scripts/diagnostics/`에 있다. 실제 DB 회귀는 `scripts/verification/verify_request_training.py`의 임시 기록 스키마 경로를 사용한다. AI 호출이 포함된 검증 도구는 호출 횟수와 비용을 확인한 뒤 실행한다.

파일 이동 시 보고서의 상대 링크와 검증 도구의 읽기·쓰기 경로를 함께 수정한다. 이전 검증 결과의 날짜와 판정은 보존한다.
