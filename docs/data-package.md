# 첫 프로토타입 데이터 패키지

첫 배포는 `training-001/v1`, 총 users 200행이다. 가입 코호트는 2026-09-01 00:00 이상~09-15 00:00 미만(KST), 수집 완료 경계는 09-19 00:00 KST다. 경계 검증을 위해 200행 중 한 유저는 코호트 종료 시각에 가입하여 분석에서 제외한다. 생성기는 고정 seed와 생성기 버전으로 CSV를 재현한다. 범주는 android/ios/pc, KR, organic/ad/referral, 앱 버전 1.0.0이다. 로그 시각은 UTC ISO 8601 초 단위다.

`packages/<id>/<version>/public`에는 CSV, 문제, 데이터 사전과 공개 manifest만 둔다. `private`에는 기준 SQL, Python으로 계산한 기대 결과, 선택적 힌트, 생성 조건, 비공개 manifest 및 검증 영수증을 둔다. 이 디렉터리 전체를 정적 파일 서버에 연결하면 안 된다. 웹/API에서는 공개 manifest와 공개 문제만 명시적으로 응답한다. evaluator만 `.reference()`를 사용할 수 있다.

새 배포는 `python scripts/generate_package.py`로 만들며 같은 ID/버전의 기존 디렉터리는 덮어쓰지 않는다. 파일 생성만으로 출제할 수 없다. `python scripts/validate_package.py --dsn <관리자 DB 연결>`로 공개 표 적재, PostgreSQL 기준 SQL 실행, 별도 Python 집계와 일치 검증을 수행해야 한다. 관리 DB에는 learner 역할이 먼저 존재해야 한다. 검증 영수증은 두 manifest의 해시를 고정하고 파일 변경 시 로더가 차단한다. 내용 수정은 새 release_version으로 배포한다.

`PackageCatalog(root).load(id, version)`은 출제 가능한 패키지만 읽는다. 검증 도구의 `allow_unvalidated=True`만 미검증 자료를 허용한다. `.list_public()`은 검증 실패/미검증 패키지를 제외한다. 반환 Package의 `.public`과 `.problem(id)`만 일반 코칭에 전달한다. `.schema_name`은 ID/버전별 DB 스키마이며 v2를 추가해도 v1 시도는 기존 스키마를 고정한다.

경계 fixture에는 D0만 접속, D1 00:00, D7 마지막 초, D8 00:00, D3만 접속, 반복 로그인, D0 시작~D1 종료, 코호트 종료 가입, 수집 경계와 D8이 같은 경우 및 미완료 경우가 포함된다. `tests/test_churn.py`는 빈 분모와 가입 경계도 별도로 검사한다. `tests/test_packages.py`는 변조, 혼합 ID/버전, 누락 기준자료, 경로 이탈 및 버전 고정을 검사한다. PostgreSQL 실행은 별도 검증 명령이 실제 수행한 경우에만 완료로 기록한다.
