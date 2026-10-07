# 실행 도구

- Discord 운영 배포: `python scripts/deploy_discord.py --apply`. main의 CI 성공 커밋을 Git archive로 빌드하고 기존 봇만 교체한다. `--status`로 운영 커밋을 확인한다. [배포 절차](../docs/discord-release-workflow.md).

- 이 폴더 바로 아래: 로컬 설정, API 키 입력, 훈련 데이터 생성·검증, DB 초기 준비. `verify_release.py`는 설치 과정의 패키지 지문 검사이므로 이 위치를 유지한다.
- `verification/`: 자동 회귀, 실제 모델·브라우저 검증, 결과 내보내기와 보고서 생성.
- `diagnostics/`: 모델·데이터·화면 문제 조사.

모든 도구는 프로젝트 루트에서 실행한다. 예:

```powershell
.\.venv\Scripts\python.exe scripts/verification/verify_v2_release.py --help
```

하위 폴더의 Python 도구가 파일 위치로 프로젝트 루트를 찾을 때는 `Path(__file__).resolve().parents[2]`를 사용한다. `da_agent`를 import하는 도구는 기존 Docker 환경 또는 `PYTHONPATH=src` 환경에서 실행한다.

브라우저·모델 검증 결과는 `tests/artifacts/`, 설명 문서는 `tests/reports/`에 저장한다. 기존 설치 명령과 Compose의 DB 초기화 경로는 유지한다.
