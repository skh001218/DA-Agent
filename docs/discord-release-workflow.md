# Discord 봇 Git·운영 반영 절차

운영은 GitHub `main`에 포함된 특정 커밋으로만 배포한다. 기능 브랜치 수정, 테스트, PR, main 병합, 해당 커밋 배포, 실제 동작 확인을 하나의 작업 흐름으로 진행한다. 사용자가 2026-10-07에 선택한 방식은 **main 기준 배포 명령 실행**이며 자동 배포 실행기를 설치하지 않는다.

## 개발·병합

1. `codex/` 기능 브랜치에서 코드·Spec·관련 검증을 함께 변경한다.
2. 테스트와 화면이 있는 변경의 관련 동작을 확인한다.
3. 브랜치를 push하고 main을 대상으로 PR을 만든다.
4. GitHub CI의 `automated-tests`, `discord-db-tests`, `discord-image` 성공을 확인한 뒤 PR을 병합한다.
5. main의 병합 커밋에도 CI가 성공한 뒤 배포 명령을 실행한다. Git 반영 완료와 운영 반영 완료를 각각 기록한다.

CI는 GitHub 호스팅 실행기에서 동작한다. Discord 토큰·운영 DB·Gemma API 키를 GitHub에 저장하지 않는다. DB 검증은 서비스 컨테이너의 별도 일회용 DB에서 수행한다. 이미지 검증은 봇에 로그인하지 않고 코드 지문만 검사한다.

## 운영 상태·배포

Git과 Python 3.12 이상, 실행 중인 Docker Desktop 및 기존 `da-agent-discord-bot-1` 컨테이너가 필요하다. 프로젝트 루트에서 실행한다.

```powershell
python scripts/deploy_discord.py --status
python scripts/deploy_discord.py
python scripts/deploy_discord.py --apply
```

첫 명령은 현재 운영의 커밋·이미지 ID를 읽는다. 두 번째는 최신 origin/main과 해당 커밋의 CI를 확인하고 계획만 출력한다. 세 번째가 실제 배포다. 특정 main 커밋을 선택하려면 `--commit <40자리 SHA>`를 추가한다. 이 옵션으로 이전 main 커밋도 선택할 수 있지만 해당 커밋이 새 배포 규약과 CI 검사를 충족해야 한다.

배포 명령은 다음을 확인한다.

- 대상 커밋이 main 이력에 포함되는지와 세 CI 검사 모두의 최신 결과가 성공인지 확인한다. 확인할 수 없으면 중단한다.
- `git archive`로 해당 커밋만 추출해 빌드한다. 로컬 미커밋 파일과 `.local` 환경은 빌드에 포함하지 않는다.
- 이미지에 커밋 SHA·source ref·소스 파일 목록과 SHA256을 기록한다. 이미지 내용이 추출한 Git 파일과 일치하는지 확인한다.
- 진행 중인 봇 요청이 있거나 빌드 중 다른 작업자가 운영 이미지를 바꾸면 중단한다. 동시 배포도 잠금으로 막는다.
- 기존 봇 환경 변수, 비밀 파일 마운트, 외부 영구 볼륨과 네트워크를 재사용해 bot 서비스만 교체한다. DB·웹 앱은 교체하지 않는다.
- 새 봇의 Discord 연결, 재시작 여부, 실행 파일 지문·커밋을 확인한다. 실패하면 이전 이미지와 구성으로 복구를 시도하고 결과를 기록한다.

현재 프로젝트의 공개 GitHub API에서 CI 결과를 읽는다. API 한도·네트워크 오류·CI 실패는 배포 중단 사유이며 우회 옵션은 제공하지 않는다. 비공개 저장소로 전환하면 인증된 CI 조회 어댑터를 먼저 추가해야 한다.

## 배포 기록과 복구

현재 운영 기록은 `.local/releases/current.json`, 시도별 기록은 `.local/releases/<커밋 SHA>/deployment.json`이다. 커밋·이미지 ID·검증 시각·검증 파일 수를 저장한다. `verified`는 연결·실행 코드 확인 완료를 뜻하며 기능 화면 검증 완료는 별도 검증 보고와 Spec에 기록한다.

같은 폴더의 `compose.json`, `previous-compose.json`은 기존 비밀 환경을 재사용하기 위한 로컬 전용 파일이다. **Git에 추가하지 않는다.** 자동 복구 실패 시 해당 기록의 상태와 Docker 상태를 확인한 뒤 이전 구성을 사용한다. DB·영구 볼륨을 삭제하지 않는다.

강제 종료로 `.local/releases/deploy.lock`이 남았다면 기록된 PID의 배포가 실제로 끝났는지 확인한 뒤 해당 잠금 파일만 제거한다. 실행 중인 배포의 잠금을 제거하지 않는다.

봇 시작 시 `/app/release.json`의 지문을 확인하고 커밋을 로그에 표시한다. 운영 파일을 직접 덮어쓰면 인증된 Git 버전이 아니므로 시작 검증에서 거절한다. 이전 문서의 로컬 빌드·overlay 명령은 당시 배포 기록이며 앞으로의 배포 명령으로 사용하지 않는다.

## 기능 확인

배포 후 실제 Discord에서 변경한 기능의 핵심 흐름을 확인하고 Spec을 갱신한다. 이번 포럼 변경은 본인의 저장 평가에서 `/resume session_id:...` 실행 → 같은 결과 포스트 이동 → 첫 메시지의 평가·PDF 확인 → PDF 다운로드 확인 순서다. 신규 제출에서는 평가 답글을 추가하지 않는지도 확인한다.

근거: [GitHub Python CI](https://docs.github.com/en/actions/tutorials/build-and-test-code/python), [배포 관리](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments).
