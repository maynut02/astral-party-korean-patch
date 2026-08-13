# 운영 설정 및 배포 절차

이 문서는 새 GitHub 저장소를 연결한 뒤 Astral Party 한국어 패치 자동화 파이프라인을 실제 운영하는 절차를 정리합니다.

## 1. GitHub 저장소 설정

로컬 `main` 브랜치를 새 GitHub 저장소에 연결하고 push합니다.

필수 Repository Variable:

- `GAME_VERSION`: hotaddress 조회에 사용할 게임 버전. 현재 기준 `3.2.0`.

필수 Actions Secrets:

- `NEON_DATABASE_URL`: Builder가 source/translation/build 데이터를 읽고 쓰는 Neon PostgreSQL 연결 문자열.
- `NEON_DATABASE_URL_DIRECT`: migration 실행용 direct PostgreSQL 연결 문자열.
- `STEAM_USERNAME`: Astral Party 라이선스를 가진 Steam 계정명.
- `STEAM_REFRESH_TOKEN`: DepotDownloader persistent Steam refresh token. Steam 비밀번호가 아님.

Secret 값은 소스, issue, workflow log, 채팅에 붙여넣지 않습니다.

## 2. Steam refresh token 최초 생성

Steam 비밀번호를 GitHub Actions에 저장하지 않습니다. 최초 1회 WSL/로컬 환경에서 다음 스크립트를 실행합니다.

```bash
./scripts/bootstrap-steam-token.sh
```

Steam 모바일 앱에서 QR 로그인을 승인하면 다음 파일이 생성됩니다.

```text
.secrets/steam-refresh-token.txt
```

`.secrets/`는 Git에서 제외되어 있습니다. GitHub CLI가 연결된 환경에서는 다음처럼 등록할 수 있습니다.

```bash
gh secret set STEAM_REFRESH_TOKEN < .secrets/steam-refresh-token.txt
gh secret set STEAM_USERNAME
```

Steam에서 token을 폐기하거나 만료된 경우 같은 절차로 새 token을 생성해 Secret만 교체합니다.

## 3. Preview 자동 파이프라인

`.github/workflows/check-game.yml`은 6시간마다 실행되며 수동 실행도 가능합니다.

새 revision이 없으면 check에서 종료합니다. 새 revision이 확인되면 다음 순서가 자동으로 이어집니다.

```text
check-game
  -> sync-game
  -> fetch-legacy-data
  -> build-patch (preview)
  -> independent validation
  -> transactional GitHub Release
  -> patch-index/release-index.json update
  -> Neon build status = released
```

자동 preview tag 형식:

```text
v<game-version>-r<revision>-preview.<github-run-number>
```

예:

```text
v3.2.0-r116-preview.42
```

Preview build는 기존 한국어 번역이 있으면 적용하며 source가 변경돼 review가 필요한 번역도 preview에서는 확인 목적으로 사용할 수 있습니다.

## 4. Stable 수동 승격

Stable은 자동으로 배포하지 않습니다. 번역 검수 후 `Release Stable Patch` workflow를 수동 실행합니다.

입력:

- `revision_id`: Neon에 저장된 processed game revision UUID.
- `patch_version`: 한 번 사용하면 바꾸지 않는 GitHub Release tag.

권장 예:

```text
v3.2.0-r116-stable.1
```

Stable build는 현재 source fingerprint와 일치하는 `approved` 번역만 적용합니다.

Stable workflow는 내부적으로 Steam `data.unity3d` 취득, build, 독립 검증, Release, release index 갱신을 한 번에 수행합니다.

## 5. Patch Release의 불변성

Patch Release는 draft 상태에서 생성합니다. 모든 patch asset과 `manifest.json` 업로드가 성공해야 공개 상태로 전환됩니다.

업로드 중 오류가 발생하면 draft Release와 tag를 정리합니다. 이미 공개된 동일 tag Release는 덮어쓰지 않습니다.

`patch-index` Release의 `release-index.json`만 의도적으로 mutable하며, Patcher가 compatible patch를 찾는 작은 index 역할을 합니다.

Patcher에서 사용할 index URL은 저장소가 확정되면 다음 형식입니다.

```text
https://github.com/<owner>/<repo>/releases/download/patch-index/release-index.json
```

`Build Patcher` workflow는 이 URL을 `ASTRAL_PATCH_INDEX_URL` compile-time 값으로 `AstralAutoPatcher.exe`에 자동 내장합니다. URI에서는 외부 download URL을 받지 않으며 Patcher가 이 고정 release index만 조회합니다.

## 6. AstralAutoPatcher portable EXE

`Build Patcher` workflow는 Node, Vite, Tauri, NSIS 없이 `patcher/Cargo.toml`의 version을 읽고 Windows runner에서 다음 검증을 수행합니다.

```text
cargo fmt --all -- --check
cargo test --all-targets --all-features
cargo clippy --all-targets --all-features -- -D warnings
cargo build --release --bin AstralAutoPatcher
```

산출물은 단일 `AstralAutoPatcher.exe`와 `.sha256` 파일입니다. 최초 실행 위치는 자유롭습니다. 프로그램은 자신을 다음 위치에 복사하고 사용자별 `astral://` protocol handler를 등록합니다. 관리자 권한은 요구하지 않습니다.

```text
%LOCALAPPDATA%\AstralAutoPatcher\AstralAutoPatcher.exe
```

같은 폴더에 `settings.json`, `installed.json`, staging/backup 상태를 관리합니다. 최초 실행 시 Steam과 LocalLow 경로를 자동 감지하고, 프로그램 설정 메뉴에서 두 경로를 각각 수정할 수 있습니다. 패치가 설치된 동안에는 ownership 안전성을 위해 경로 변경을 차단합니다.

지원 URI는 고정 allowlist입니다.

```text
astral://install
astral://remove
astral://settings
```

`astral://install`은 저장된 patch channel과 내장 release index를 사용해 호환 패치를 설치/업데이트하고, `astral://settings`는 경로 및 채널 설정 메뉴를 엽니다.

기본 실행은 Actions artifact만 생성합니다. `publish=true`로 수동 실행하면 다음 tag로 immutable GitHub Release를 생성합니다.

```text
patcher-v<version>
```

## 7. 게임 버전 상승

현재 revision은 자동 감지하지만 게임 version 자체는 `GAME_VERSION` Repository Variable로 관리합니다.

예를 들어 게임이 `3.2.0`에서 `3.3.0`으로 올라가면:

```text
GAME_VERSION=3.3.0
```

으로 변경합니다. 이후 revision/catalog/bundle 탐색은 다시 자동으로 진행됩니다.

## 8. Neon migration

`check-game.yml`과 `sync-game.yml`은 DB 작업 전에 migration runner를 실행합니다.

Migration 파일은 적용 후 내용을 수정하지 않고 새 번호 파일을 추가합니다.

현재 migration:

```text
0001_initial.sql
0002_rebuildable_builds.sql
```

Patcher 애플리케이션은 Neon credential을 받지 않으며 DB에 직접 접속하지 않습니다.

## 9. 최초 운영 체크리스트

1. 새 GitHub 저장소를 만들고 `main`을 push.
2. Neon database를 만들고 `NEON_DATABASE_URL`, `NEON_DATABASE_URL_DIRECT` 등록.
3. `GAME_VERSION` 등록.
4. `bootstrap-steam-token.sh`로 refresh token 생성.
5. `STEAM_USERNAME`, `STEAM_REFRESH_TOKEN` 등록.
6. `CI` workflow 통과 확인.
7. `Check Game` workflow 수동 실행.
8. Preview Release와 `patch-index` 생성 확인.
9. `Build Patcher`로 생성한 `AstralAutoPatcher.exe`를 실행해 self-registration, `astral://` URI, Steam/LocalLow 경로 탐지, install/remove를 실게임 검증.
10. 검수 완료 후 `Release Stable Patch`를 수동 실행.

실제 credential과 GitHub repository가 연결되기 전까지 로컬 구현만으로는 6~10번의 hosted integration 검증을 수행할 수 없습니다.
