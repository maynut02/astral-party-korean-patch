# Astral Party Korean Patch

Astral Party 한국어 패치를 자동으로 생성하고 안전하게 설치하는 monorepo입니다.

- `builder/`: GitHub Actions에서 실행되는 게임 리소스 탐색, 추출, DB 동기화, 패치 생성 및 검증 도구
- `patcher/`: 사용자 PC에서 패치를 설치/업데이트/제거하는 Tauri 애플리케이션
- `database/`: Neon PostgreSQL 스키마와 migration
- `schemas/`: Builder와 Patcher가 공유하는 manifest 및 설정 스키마
- `routes/`: 게임 배포 경로별 선언적 설정
- `.github/workflows/`: 자동 감지, 동기화, 빌드, 배포 파이프라인

운영 빌드는 로컬 게임 파일을 입력으로 사용하지 않습니다. 게임 에셋 수집부터 패치 생성까지 GitHub Actions에서 수행합니다.

자세한 구현 계획은 [`docs/PLAN.md`](docs/PLAN.md)를 참조하세요.
