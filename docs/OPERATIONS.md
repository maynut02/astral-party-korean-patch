# 운영 설정 및 배포 절차

이 문서는 Astral Party 한국어 패치의 INT_STEAM, CN_STEAM, INT_ANDROID 자동화와 AstralAutoPatcher/Android 내장 패처 운영 절차를 정리합니다.

## 1. GitHub 저장소 설정

필수 Repository Variable:

- `GAME_VERSION`: hotaddress 조회에 사용할 게임 버전. 현재 기준 `3.2.0`.

필수 Actions Secrets:

- `NEON_DATABASE_URL`: Builder가 source/translation/build 데이터를 읽고 쓰는 Neon PostgreSQL 연결 문자열.
- `NEON_DATABASE_URL_DIRECT`: migration 실행용 direct PostgreSQL 연결 문자열.
- `STEAM_USERNAME`: Astral Party 라이선스를 가진 Steam 계정명.
- `STEAM_REFRESH_TOKEN`: DepotDownloader persistent Steam refresh token. Steam 비밀번호가 아님.
- `ANDROID_KEYSTORE_BASE64`: INT_ANDROID 한국어 APK에 계속 사용할 signing keystore의 base64.
- `ANDROID_KEYSTORE_PASSWORD`: Android keystore 비밀번호.
- `ANDROID_KEY_ALIAS`: Android signing key alias.
- `ANDROID_KEY_PASSWORD`: Android signing key 비밀번호.

Secret 값은 소스, issue, workflow log, 채팅에 붙여넣지 않습니다.

## 2. Steam refresh token 최초 생성

Steam 비밀번호를 GitHub Actions에 저장하지 않습니다. 최초 1회 WSL/로컬 환경에서 다음 스크립트를 실행합니다.

```bash
./scripts/bootstrap-steam-token.sh
```

Steam 모바일 앱에서 QR 로그인을 승인하면 `.secrets/steam-refresh-token.txt`가 생성됩니다. `.secrets/`는 Git에서 제외되어 있습니다.

```bash
gh secret set STEAM_REFRESH_TOKEN < .secrets/steam-refresh-token.txt
gh secret set STEAM_USERNAME
```

## 3. Release / Develop 정책

패치의 distribution channel은 다음 두 개입니다.

- `release`: 일반 사용자가 Windows AstralAutoPatcher 또는 INT_ANDROID 내장 런타임으로 받는 배포 채널.
- `develop`: 내부 개발/검증용 채널. 일반 AutoPatcher는 조회하지 않음.

**어느 채널에서도 승인되지 않은 번역은 패치에 포함하지 않습니다.** 현재 source fingerprint와 일치하고 상태가 `approved`인 번역만 적용하며, 신규/미번역/draft/reviewed/needs_review 항목은 게임 원문을 유지합니다.

`release` 채널에는 생성 목적에 따라 두 종류의 tag가 있습니다.

자동 호환판:

```text
v<game-version>-r<revision>-pre
```

예:

```text
v3.2.0-r117-pre
```

새 게임 revision 감지 직후 현재까지 승인된 번역만 사용해 즉시 생성합니다. 번역 완료 여부와 관계없이 새 게임과 호환되는 패치를 제공하는 것이 목적입니다.

수동 정식판:

```text
v<game-version>-r<revision>-release
```

같은 revision에 정식 수정판이 추가로 필요하면 자동으로 다음 이름을 사용합니다.

```text
v3.2.0-r117-release.2
v3.2.0-r117-release.3
```

같은 revision/channel의 release index entry는 새 배포가 이전 entry를 교체합니다. 따라서 `-release`가 생성되면 일반 AutoPatcher는 더 이상 같은 revision의 `-pre`를 선택하지 않습니다.

## 4. Check Game 자동 파이프라인

`.github/workflows/check-game.yml`에는 GitHub 자체 `schedule`을 두지 않습니다. 외부 GCP Cloud Scheduler가 GitHub Actions `workflow_dispatch`를 호출하는 방식으로 운영합니다. GitHub Actions 화면에서 수동 실행하는 것도 가능합니다.

중복 실행은 `check-game-routes` concurrency group으로 직렬화합니다. INT_STEAM을 먼저 처리하고 같은 canonical source를 사용하는 CN_STEAM과 INT_ANDROID를 뒤에서 처리합니다.

새 revision이 없고 해당 revision에 이미 released `release` build가 있으면 check에서 종료합니다. revision이 새로 발견됐거나 sync 이후 build/release가 완료되지 않은 상태라면 같은 revision도 다시 처리해 다음 Scheduler 주기에서 자동 재시도합니다.

```text
GCP Cloud Scheduler
  -> Check Game
  -> sync-game
  -> Steam routes: fetch-legacy-data
  -> INT_ANDROID: legacy data 단계 생략
  -> build-patch (channel=release)
  -> approved 번역만 적용
  -> independent validation
  -> v<game>-r<revision>-pre Release
  -> patch-index/release-index.json update
  -> Neon build status = released
```

## 5. 번역 완료 후 정식 Release

번역 승인 자체는 Release를 만들지 않습니다. 번역 작업이 완료됐다고 판단한 시점에 GitHub Actions의 `Release Patch` workflow 버튼을 직접 누릅니다.

workflow 입력에서 `INT_STEAM`, `CN_STEAM`, `INT_ANDROID` route를 선택합니다.

1. Neon에서 선택 route의 최신 processed revision을 찾음.
2. 해당 revision의 모든 translation unit이 현재 source fingerprint 기준 `approved`인지 검사하며 하나라도 미완료면 중단.
3. route별 `v<game>-r<revision>-release` tag를 선택하며 CN_STEAM/INT_ANDROID에는 route suffix를 사용함.
4. 동일 tag가 이미 있으면 `.2`, `.3` 순으로 사용 가능한 다음 tag를 선택함.
5. Steam route만 Legacy data를 취득하고 INT_ANDROID는 생략.
6. `approved` 번역만 사용해 빌드/검증.
7. immutable GitHub Release 배포.
8. `release-index.json`의 해당 release entry를 교체.

즉 승인 버튼을 누를 때마다 빌드하지 않고, 운영자가 원하는 시점에만 정식 패치를 생성합니다.

## 6. Patch Release와 release index

Patch Release는 draft 상태에서 생성합니다. 모든 patch asset과 `manifest.json` 업로드가 성공해야 공개 상태로 전환됩니다. 업로드 중 오류가 발생하면 draft Release와 tag를 정리하며 이미 공개된 동일 tag는 덮어쓰지 않습니다.

`-pre` tag 또는 `develop` channel Release는 GitHub prerelease로 표시합니다. 정식 `-release`, `-release.2` 등은 일반 Release입니다.

Patch manifest schema v2의 각 Unity payload는 개별 `.gz` transport asset으로 배포합니다. manifest에는 압축본의 `downloadSize`/`downloadSha256`과 압축 해제 후 실제 설치 파일의 `size`/`sha256`을 기록합니다. 원본 파일이 알려진 entry에는 `sourceSize`/`sourceSha256`도 기록하며 INT_ANDROID 내장 런타임은 이 값으로 인게임 다운로드 완료 여부와 적용 전 원본 호환성을 검증합니다.

`patch-index` Release의 `release-index.json`만 의도적으로 mutable하며 Patcher가 compatible patch를 찾는 작은 index 역할을 합니다. 새 channel 체계로 처음 갱신할 때 과거 `preview/stable` index entry는 제거하고 새 `release/develop` entry만 기록합니다.

```text
https://github.com/<owner>/<repo>/releases/download/patch-index/release-index.json
```

`Build Patcher` workflow는 이 URL을 `ASTRAL_PATCH_INDEX_URL` compile-time 값으로 `AstralAutoPatcher.exe`에 자동 내장합니다.

## 7. AstralAutoPatcher portable EXE (Steam routes)

`Build Patcher` workflow는 `patcher/Cargo.toml`의 version을 읽고 Windows runner에서 다음 검증을 수행합니다.

```text
cargo fmt --all -- --check
cargo test --locked --all-targets --all-features
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo build --locked --release --bin AstralAutoPatcher
```

Actions artifact에는 `AstralAutoPatcher.exe`만 포함합니다. `publish=true`로 배포하는 버전별 Patcher Release에도 사용자용 asset은 `AstralAutoPatcher.exe` 하나만 올립니다. SHA-256과 파일 크기는 workflow 내부에서 계산해 자동 업데이트용 `patcher-index/patcher-index.json`에 기록하며 별도 `.sha256` 파일은 배포하지 않습니다. 프로그램은 자신을 다음 위치에 복사하고 사용자별 `astral://` protocol handler를 등록합니다.

```text
%LOCALAPPDATA%\AstralAutoPatcher\AstralAutoPatcher.exe
```

같은 폴더에 `settings.json`과 route별 installed/staging/backup 상태를 관리합니다. 최초 실행에서는 Steam 설치 구조를 기준으로 route를 감지합니다. CN_STEAM만 설치되어 있으면 CN_STEAM을 자동 선택하고, INT_STEAM과 CN_STEAM이 모두 설치되어 있으면 기존 선택을 유지합니다. 새 설정의 기본 선택은 INT_STEAM입니다. 선택된 route의 LocalLow는 각각 `AstralParty_INT`, `AstralParty_CN`에서 감지합니다. Steam의 공통 저장 루트는 동일할 수 있지만 UI에는 실제 route 실행 디렉터리인 `8vJXnINT` 또는 `8vJXn6CN`까지 표시합니다.

Patcher 0.4.0부터 Ratatui/Crossterm 기반 고정 화면 TUI를 사용하고, 0.4.1부터 설치 대상 patch version/asset 목록과 다운로드·적용 진행률을 표시합니다. 0.5.0부터 사용자 channel 선택을 제거하고 항상 `release` channel의 현재 게임 version/catalog에 맞는 최신 entry만 설치합니다. 0.6.0부터 시작 시 `patcher-index.json`을 확인해 더 높은 semantic version이 있으면 새 EXE를 다운로드하고 size/SHA-256을 검증한 뒤 자기 자신을 교체하고 원래 실행 인수를 유지해 재실행합니다. 0.7.0부터 INT_STEAM/CN_STEAM route별 설정과 설치 상태를 분리하고 settings schema v3를 사용합니다. 기존 schema v1/v2 경로는 INT_STEAM 설정으로 자동 이관됩니다. 업데이트 확인이나 다운로드가 실패하면 현재 버전 실행을 차단하지 않습니다.

상태 영역에는 Patcher 경로, Steam/LocalLow 경로, 게임 버전, Catalog hash, 설치된 패치 버전을 표시합니다.

자동 업데이트용 `patcher-index`는 사용자용 버전 Release와 분리된 고정 prerelease입니다. 이 Release에는 `patcher-index.json` 하나만 mutable asset으로 유지합니다. 버전별 `patcher-v<version>` Release는 immutable이며 `AstralAutoPatcher.exe` 하나만 포함합니다. `Build Patcher`를 `publish=true`로 실행하면 버전 Release 공개가 성공한 뒤 마지막 단계에서 `patcher-index.json`을 갱신하므로, index가 아직 존재하지 않는 EXE를 가리키는 상태가 생기지 않습니다.

지원 URI:

```text
astral://install
astral://remove
astral://settings

astral://install/INT_STEAM
astral://install/CN_STEAM
astral://remove/INT_STEAM
astral://remove/CN_STEAM
astral://settings/INT_STEAM
astral://settings/CN_STEAM
```

route가 없는 기존 URI는 현재 선택된 route를 사용합니다. route가 포함된 install/remove URI는 설정의 기본 route를 바꾸지 않고 해당 작업에만 지정 route를 사용합니다. `settings/<route>`는 지정 route의 설정 화면을 엽니다. query/fragment나 알 수 없는 route는 허용하지 않습니다.

## 8. INT_ANDROID 한국어 APK

Route revision은 서로 독립적으로 추적합니다. `INT_STEAM`, `CN_STEAM`, `INT_ANDROID`의 revision 번호가 서로 달라도 정상이며, `canonicalFallbackRoute`는 같은 `game_version`에서 가장 최근에 처리된 fallback route revision의 원문을 보충용으로 사용합니다. fallback 대상 route와 현재 route의 revision 번호가 같을 필요는 없습니다.

INT_ANDROID는 Windows AstralAutoPatcher나 ADB를 사용하지 않습니다. `Build Android Korean Game APK` workflow가 `apkeep`으로 Google Play의 현재 APK/split APK를 취득하고 APKEditor로 standalone APK를 만든 뒤, APK 내부 `MochiyPopOne-Regular` TTF를 교체하고 `AstralPatchRuntime`을 주입해 장기간 유지할 동일 signing key로 재서명합니다. `tools/android/build_game_apk.py`는 이미 병합된 standalone APK를 입력으로 받습니다.

Addressables 패치는 APK 내부 `BootstrapActivity`가 자기 app-specific storage의 `com.unity.addressables`를 직접 처리합니다. 최초 설치 또는 새 인게임 리소스가 아직 내려받아지는 중이면 Unity 게임을 먼저 실행합니다. 게임 실행 중 `UpdateWatcher`는 새 catalog와 원본 bundle 준비 상태를 관찰하되 파일을 수정하지 않습니다. manifest의 모든 `sourceSize`/`sourceSha256`이 일치하면 재실행 안내를 표시하고, 다음 실행에서 Unity 시작 전에 LANG/STR/TMP bundle을 검증/백업/교체합니다.

APK 자체가 업데이트되지 않고 Addressables catalog만 바뀌는 경우에는 새 한국어 APK가 필요하지 않습니다. 새 INT_ANDROID patch release만 생성하면 기존 APK의 내장 런타임이 새 catalog용 패치를 적용합니다. 상세 구조와 빌드 방법은 `docs/ANDROID.md`를 참조합니다.

## 9. 게임 버전 상승

revision은 자동 감지하지만 게임 version 자체는 `GAME_VERSION` Repository Variable로 관리합니다.

게임이 `3.2.0`에서 `3.3.0`으로 올라가면 다음 값만 변경합니다.

```text
GAME_VERSION=3.3.0
```

이후 revision/catalog/bundle 탐색은 다시 자동으로 진행됩니다.

## 10. Neon migration

`check-game.yml`, `sync-game.yml`, `release.yml`은 필요한 DB 작업 전에 migration runner를 실행합니다.

현재 migration:

```text
0001_initial.sql
0002_rebuildable_builds.sql
0003_release_channels.sql
0004_approve_legacy_imports.sql
```

`0003_release_channels.sql`은 기존 `preview/stable` build 기록을 모두 `develop` 이력으로 이동합니다. 기존 manifest의 channel 값은 새 protocol과 다르므로 과거 배포를 `release`로 가장하지 않고 현재 revision도 새 `-pre`를 다시 생성하게 합니다.

`0004_approve_legacy_imports.sql`은 기존 정식 한글패치에서 `legacy-import` actor로 이관됐고 이후 다른 수정 이력이 없는 draft 번역만 일회성으로 `approved` 승격합니다. 일반 draft 번역은 건드리지 않습니다.

Patcher 애플리케이션은 Neon credential을 받지 않으며 DB에 직접 접속하지 않습니다.

## 11. 운영 체크리스트

1. `main` push 및 CI 통과 확인.
2. `GAME_VERSION`, Neon Secrets, Steam Secrets 확인.
3. GCP Cloud Scheduler에서 `Check Game` workflow dispatch 호출 설정.
4. `Check Game`을 한 번 수동 실행해 migration과 revision check 확인.
5. 현재 revision에 새 `-pre`가 필요한데 이미 revision이 processed 상태라면 번역 상태를 확인한 뒤 `Release Patch` 버튼으로 현재 revision의 정식 release를 생성.
6. `patch-index/release-index.json`에 `channel=release` entry가 생성됐는지 확인.
7. `Build Patcher`로 `AstralAutoPatcher.exe`를 만들고 Steam install/update/remove 및 `astral://` URI를 실게임에서 검증.
8. `Build Android Korean Game APK`가 Google Play에서 현재 APK를 자동 취득·병합·패치하는지 확인하고, 동일 signing key 유지 및 최초/인게임 Addressables 다운로드 후 재실행 패치를 실기기에서 검증.
9. 이후 게임 revision 변경은 GCP Scheduler -> `Check Game` -> `-pre` 자동 생성으로 운영.
10. 번역 완료 시에만 `Release Patch` 버튼으로 정식 `-release`를 생성.
