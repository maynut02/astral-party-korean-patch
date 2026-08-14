# Astral Party Korean Patch

Astral Party 한국어 패치를 자동으로 생성하고 안전하게 설치하는 monorepo입니다.

- `builder/`: GitHub Actions에서 실행되는 게임 리소스 탐색, 추출, DB 동기화, 패치 생성 및 검증 도구
- `patcher/`: Android USB/앱플레이어 원클릭 설치, Steam 패치 설치/제거, 자동 업데이트와 `astral://` 웹 연동을 제공하는 단일 Rust TUI `AstralAutoPatcher.exe`
- `database/`: Neon PostgreSQL 스키마와 migration
- `schemas/`: Builder와 Patcher가 공유하는 manifest 및 설정 스키마
- `routes/`: 게임 배포 경로별 선언적 설정
- `.github/workflows/`: `CI`, `Patch`, `AutoPatcher`, `INT_ANDROID APK` 네 운영 workflow

운영 빌드는 로컬 게임 파일을 입력으로 사용하지 않습니다. 번역 원문은 INT_STEAM 하나를 canonical source로 관리하고, CN_STEAM/INT_ANDROID는 route별 호환 대상만 추적합니다. 게임 에셋 수집부터 패치 생성까지 GitHub Actions에서 수행합니다.

자세한 구현 계획은 [`docs/PLAN.md`](docs/PLAN.md)를 참조하세요. 실제 GitHub/Neon/Steam 운영 설정은 [`docs/OPERATIONS.md`](docs/OPERATIONS.md)에 정리되어 있습니다.
