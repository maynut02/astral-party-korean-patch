# 운영 설정 및 배포 절차

이 문서는 Astral Party 한국어 패치의 `INT_STEAM`, `CN_STEAM`, `INT_ANDROID`, `AstralAutoPatcher`, Android APK 운영 절차를 설명합니다.

## 1. 운영 구조

GitHub Actions 화면에 노출되는 운영 workflow는 네 개만 유지합니다.

| Workflow | 실행 방식 | 역할 |
| --- | --- | --- |
| `CI` | main push / PR | Builder와 AutoPatcher 검증 |
| `Patch` | pre 자동, release 수동 | 세 route의 한글패치 파일을 한 Release로 빌드/배포 |
| `AutoPatcher` | 수동 | Windows `AstralAutoPatcher.exe` 빌드/배포 |
| `INT_ANDROID APK` | 수동 | Google Play 기반 Android APK 빌드/배포 |

이전의 `Check Game`, `Sync Game`, `Fetch Legacy Game Data`, `Build Patch`, `Publish Patch`, route별 `Release Patch` workflow는 제거했습니다. check/sync/build/validate/publish는 `Patch` 안에서 하나의 원자적인 배포 흐름으로 처리합니다.

사용자에게 보이는 정식 Release 이름은 다음 형식을 사용합니다.

```text
AutoPatcher v0.8.x

INT_ANDROID v3.2.0
└─ AstralParty_INT_ANDROID.apk

v3.2.0_r116
├─ INT_STEAM_manifest.json
├─ CN_STEAM_manifest.json
├─ INT_ANDROID_manifest.json
└─ route별 patch payload
```

자동 호환판은 같은 형식에 `-pre`를 붙입니다.

```text
v3.2.0_r116-pre
```

## 2. Repository 설정

필수 Repository Variable:

- `GAME_VERSION`: hotaddress 조회에 사용할 게임 버전. 예: `3.2.0`.

필수 Actions Secrets:

- `NEON_DATABASE_URL`: Builder runtime용 Neon PostgreSQL 연결 문자열.
- `NEON_DATABASE_URL_DIRECT`: migration 실행용 direct PostgreSQL 연결 문자열.
- `STEAM_USERNAME`: Astral Party 라이선스를 가진 Steam 계정명.
- `STEAM_REFRESH_TOKEN`: DepotDownloader persistent Steam refresh token.
- `PLAY_EMAIL`: Google Play APK 취득 계정.
- `AAS_TOKEN`: `apkeep` Google Play 인증 token.
- `ANDROID_KEYSTORE_BASE64`: Android 한국어 APK에 계속 사용할 signing keystore.
- `ANDROID_KEYSTORE_PASSWORD`: Android keystore 비밀번호.
- `ANDROID_KEY_ALIAS`: Android signing key alias.
- `ANDROID_KEY_PASSWORD`: Android signing key 비밀번호.

Secret 값은 소스나 workflow log에 기록하지 않습니다.

## 3. Steam refresh token 최초 생성

Steam 비밀번호를 GitHub Actions에 저장하지 않습니다. 최초 1회 로컬에서 다음 스크립트를 실행합니다.

```bash
./scripts/bootstrap-steam-token.sh
```

Steam 모바일 앱에서 로그인을 승인한 뒤 생성된 token을 등록합니다.

```bash
gh secret set STEAM_REFRESH_TOKEN < .secrets/steam-refresh-token.txt
gh secret set STEAM_USERNAME
```

## 4. INT_STEAM 원문과 revision 변경 기록

번역 원문의 canonical source는 **`INT_STEAM` 하나뿐**입니다. `CN_STEAM`과 `INT_ANDROID`는 route별 catalog/bundle 호환 metadata만 저장합니다.

```text
INT_STEAM game_revisions r116        <- 원문 변경 그룹
  └─ source_changes
       ├─ added
       ├─ modified
       └─ removed

translation_units
  └─ current_source_version_id -> source_versions

CN_STEAM / INT_ANDROID
  └─ game_revisions + asset_locations only
```

새 INT_STEAM revision을 탐색할 때 전체 원문을 revision마다 복제하지 않습니다. 각 unit의 현재 `source_fingerprint`와 새 scan을 비교해서 실제 변경만 저장합니다.

```text
동일 fingerprint
  -> DB 원문 row 추가 없음

신규/변경 fingerprint
  -> source_versions에 새 버전 저장
  -> source_changes에 현재 revision 소속 change 기록
  -> current_source_version_id 즉시 교체

삭제된 unit
  -> source_changes(change_type=removed)
  -> current_source_version_id=NULL
  -> 과거 source_versions는 유지
```

`source_changes.status`는 `applied`만 허용합니다. 게임 원문은 외부 사실이므로 번역처럼 승인 대기하지 않고 탐지한 revision에서 즉시 현재 상태로 적용됩니다. 따라서 웹사이트에서 원문 변경 이력을 표시할 때 `game_revisions` 하나를 그대로 change group으로 사용하면 됩니다.

세 route의 patch build는 현재 INT_STEAM 원문과 같은 `translations` production table을 사용합니다. route별 번역 snapshot/승인율은 존재하지 않습니다.

## 5. 번역 제안/승인과 Patch 선택 규칙

`translations`는 **승인된 현재 production 번역 전용 테이블**입니다. `status` 컬럼이 없으며 row가 존재한다는 것 자체가 승인되어 패치에 사용 가능한 값이라는 의미입니다.

새 번역이나 기존 번역 수정은 먼저 `translation_changes`에 들어갑니다.

```text
사용자 수정
  -> translation_change_groups
  -> translation_changes(status=pending)
  -> translations는 변경 없음
```

검토 결과에 따라:

```text
pending -> approved
        -> rejected
        -> superseded
```

승인 시 하나의 DB transaction 안에서 change를 `approved`로 바꾸고 `translations`를 INSERT/UPDATE합니다. DB trigger도 `translations.applied_change_id`가 같은 unit/locale/text/source를 가진 approved change인지 검증하므로 향후 웹사이트가 production table을 승인 절차 없이 직접 변경할 수 없습니다.

원문이 바뀌어도 기존 승인 번역은 자동 폐기하지 않습니다. 제안 이후 원문이 다시 바뀐 경우 그 stale pending proposal은 승인할 수 없고 현재 source version 기준으로 새 제안을 만들어야 합니다.

Patch Builder의 선택 규칙은 의도적으로 단순합니다.

```text
translations에 승인 번역 있음
  -> 승인 번역 사용

translations에 승인 번역 없음
  -> 현재 게임 원문 사용
```

따라서 다음 경우도 자동으로 처리됩니다.

```text
현재 승인 번역 = "게임 시작"
최신 수정 제안 = "시작하기" (pending)
-> Patch는 "게임 시작" 사용

승인 번역 없음
최신 수정 제안 = "시작하기" (pending)
-> Patch는 게임 원문 사용

최신 수정 제안을 승인
-> translations가 "시작하기"로 갱신
-> 다음 Patch부터 "시작하기" 사용
```

`astral-builder translation-status`는 현재 unit 수, production 번역 수, 미번역 수, pending proposal 수를 보고합니다. 미번역 unit은 원문 fallback이 가능하므로 일반 Release를 막지 않습니다. 모든 current unit에 production 번역이 있어야 하는 별도 검증이 필요할 때만 `--strict`를 사용합니다.

```bash
astral-builder translation-status \
  --revision-id <INT_STEAM_REVISION_UUID> \
  --strict
```

## 6. 자동 Pre 배포

`Patch` workflow는 다음 schedule을 자체적으로 갖습니다.

```text
17 */6 * * *
```

즉 6시간마다 `INT_STEAM`만 update check합니다. route별 DB check workflow는 더 이상 없습니다.

새 canonical revision이 있거나 이전 자동 배포가 완료되지 않은 경우에만 다음 작업을 실행합니다.

```text
INT_STEAM update check
  -> migration
  -> INT_STEAM canonical source sync
  -> CN_STEAM target compatibility sync
  -> INT_ANDROID target compatibility sync
  -> translation status report
  -> Steam data.unity3d 2개를 한 번의 DepotDownloader 실행으로 취득
  -> INT_STEAM / CN_STEAM / INT_ANDROID build
  -> 세 route 모두 independent validation
  -> 하나의 prerelease 생성
  -> release-index.json 갱신
  -> 세 build를 released 상태로 기록
```

자동 Pre는 `develop` distribution channel을 사용하고 일반 AutoPatcher/Android runtime의 기본 `release` channel을 덮어쓰지 않습니다.

Release 표시 이름:

```text
v3.2.0_r116-pre
```

Pre용 내부 tag는 `patch-pre` 하나를 rolling 방식으로 사용하므로 게임 업데이트마다 오래된 pre Release가 누적되지 않습니다. 새 빌드는 세 route 검증이 모두 끝난 뒤 기존 `patch-pre`를 교체합니다.

현재 revision에 이미 성공한 `develop` 또는 `release` build가 하나라도 있으면 자동 check는 같은 revision을 반복 빌드하지 않습니다.

## 7. 수동 정식 Patch Release

번역 작업이 완료됐다고 판단한 시점에 Actions의 `Patch`를 실행하고 `mode=release`를 선택합니다. route 선택 입력은 없습니다.

수동 정식 배포도 항상 다음 세 route를 한 번에 처리합니다.

```text
INT_STEAM
CN_STEAM
INT_ANDROID
```

세 route 중 하나라도 sync/build/validation에 실패하면 Release를 공개하지 않습니다. 모든 결과가 검증된 뒤 하나의 Release를 생성하고 세 manifest와 route-prefixed payload를 함께 올립니다.

정식 Release 표시 이름과 tag:

```text
v3.2.0_r116
```

같은 `gameVersion + canonical INT_STEAM revision`을 다시 수동 배포하면 표시 이름과 tag는 그대로 유지하고 기존 Release를 교체합니다. 교체는 세 route의 새 빌드/검증이 모두 끝난 뒤에만 시작하므로 번역 수정만 있는 같은 게임 revision에서도 `v3.2.0_r116` 하나만 유지할 수 있습니다.

## 8. Patch manifest와 release index

각 route는 독립적인 manifest를 갖습니다.

```text
INT_STEAM_manifest.json
CN_STEAM_manifest.json
INT_ANDROID_manifest.json
```

한 Release 안에서 asset 이름 충돌을 막기 위해 payload 이름에도 route prefix가 붙습니다.

```text
int-steam-lang-....bin.gz
cn-steam-str-....bin.gz
int-android-tmp-font-....bin.gz
```

manifest schema v2는 gzip transport의 `downloadSize`/`downloadSha256`과 압축 해제 후 payload의 `size`/`sha256`을 모두 기록합니다. 원본 파일을 검증할 수 있는 entry는 `sourceSize`/`sourceSha256`도 기록합니다.

새 클라이언트가 사용하는 rolling metadata는 별도 GitHub Release가 아니라 `distribution` branch에 둡니다.

```text
https://raw.githubusercontent.com/<owner>/<repo>/distribution/release-index.json
https://raw.githubusercontent.com/<owner>/<repo>/distribution/patcher-index.json
https://raw.githubusercontent.com/<owner>/<repo>/distribution/android-apk-index.json
```

이를 통해 Releases 화면에는 한글패치, AutoPatcher, APK 같은 실제 사용자 배포물만 새로 생성됩니다.

기존에 배포된 AutoPatcher/APK가 예전 `patch-index`, `patcher-index`, `android-apk-index` Release URL을 하드코딩하고 있으므로 해당 Release가 이미 존재할 경우 새 workflow가 index 파일을 **호환용으로만 mirror**합니다. workflow는 이 legacy Release들을 새로 만들지 않습니다. 기존 클라이언트 지원이 더 이상 필요 없을 때 수동으로 삭제하면 이후에도 재생성되지 않습니다.

## 9. AutoPatcher Release

Actions에서 `AutoPatcher` workflow를 수동 실행합니다. 별도 `publish` 입력은 없습니다. 수동 실행 자체가 검증 후 배포 요청입니다.

workflow는 다음을 수행합니다.

```text
cargo fmt --all -- --check
cargo test --locked --all-targets --all-features
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo build --locked --release --bin AstralAutoPatcher
```

`patcher/Cargo.toml`의 version이 `0.8.x`라면 Release 표시 이름은 다음과 같습니다.

```text
AutoPatcher v0.8.x
```

사용자용 Release asset은 `AstralAutoPatcher.exe`입니다. SHA-256과 크기는 `distribution/patcher-index.json`에 기록합니다.

프로그램은 자신을 다음 위치에 설치하고 `astral://` protocol handler를 등록합니다.

```text
%LOCALAPPDATA%\AstralAutoPatcher\AstralAutoPatcher.exe
```

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

AutoPatcher는 일반 사용자에게 항상 `release` channel의 현재 game version/catalog와 일치하는 patch만 선택합니다.

## 10. INT_ANDROID APK Release

Actions에서 `INT_ANDROID APK` workflow를 수동 실행합니다. 별도 `publish` 입력은 없습니다.

최종 산출물:

```text
INT_ANDROID v3.2.0
└─ AstralParty_INT_ANDROID.apk
```

같은 게임 version에서 APK runtime/build 로직을 수정해 다시 수동 배포하면 새 APK 검증이 끝난 뒤 동일 `INT_ANDROID v3.2.0` Release를 교체합니다. 따라서 같은 game version의 APK Release가 여러 개 누적되지 않습니다.

빌드 순서는 다음과 같습니다.

```text
Google Play split APK
  -> APKEditor standalone merge
  -> legacy font + AstralPatchRuntime 수정
  -> zipalign -p
  -> 원본 Play APK Signing Block 이식
  -> JingMatrix/LSPatch v0.8 sigbypass level 2를 마지막에 적용
  -> 장기 Android signing key로 최종 서명
  -> apksigner verify
  -> AstralParty_INT_ANDROID.apk
```

LSPatch 출력은 nested `origin.apk`/file-link를 사용하는 특수 ZIP이므로 생성 뒤 다시 열어 수정하지 않습니다.

AutoPatcher는 `distribution/android-apk-index.json`에서 APK URL/size/SHA-256/package/installer를 확인한 뒤 설치합니다. 실제 기기는 USB 디버깅과 최초 RSA 승인만 사용자가 수행합니다. MuMu/BlueStacks/LDPlayer는 실행 중인 ADB endpoint를 자동 탐색합니다.

설치 시 `installerPackageName=com.android.vending`을 지정하고 설치 뒤 다시 검증합니다. 공식 Google Play판과 한국어판 signing key가 다르면 최초 전환 시 제거가 필요할 수 있으므로 데이터 삭제 가능성을 사용자에게 확인한 뒤 진행합니다.

APK 자체가 바뀌지 않고 Addressables catalog만 갱신된 경우에는 APK를 재배포할 필요가 없습니다. 새 `INT_ANDROID` patch manifest가 포함된 정식 Patch Release를 배포하면 APK 내 runtime이 다음 실행에 이를 사용합니다.

상세 Android 구조는 [`ANDROID.md`](ANDROID.md)를 참조합니다.

## 11. 게임 버전 상승

게임 version 자체는 `GAME_VERSION` Repository Variable로 관리합니다.

예를 들어 `3.2.0`에서 `3.3.0`으로 올라가면 다음 값만 바꿉니다.

```text
GAME_VERSION=3.3.0
```

이후 canonical source와 각 route target의 revision/catalog/bundle 탐색은 `Patch` workflow가 처리합니다.

## 12. Neon migration과 전체 초기화

현재 DB는 향후 웹 번역 편집기를 고려한 새 production/audit 구조로 재설계했고 migration history를 하나의 baseline으로 squash했습니다.

```text
0001_initial.sql
```

주요 DB 객체:

```text
game_revisions
asset_locations
translation_units
source_versions
source_changes
translation_change_groups
translation_changes
translations
builds
build_files
current_source_texts (view)
translation_workbench (view)
```

기존 DB에는 과거 `0001_initial.sql` checksum이 기록되어 있기 때문에 새 코드를 기존 DB에 그대로 migration하는 것은 지원하지 않습니다. 승인 번역을 먼저 backup한 뒤 DB/Release/tag/index를 모두 초기화해서 새 baseline에서 다시 채웁니다.

전체 명령과 복구 순서는 [`RESET.md`](RESET.md)를 따릅니다.

```text
승인 번역 export
-> 새 코드 push
-> database/reset.py
-> database/migrate.py
-> GitHub Release/tag/distribution 초기화
-> Patch mode=pre로 원문 재수집
-> 승인 번역 import
-> Patch mode=release
-> INT_ANDROID APK
-> AutoPatcher
```

Patcher 애플리케이션은 Neon credential을 받지 않으며 DB에 직접 접속하지 않습니다.

## 13. 운영 체크리스트

1. `main` push 후 `CI` 통과 확인.
2. `GAME_VERSION`, Neon/Steam/Google Play/Android signing secrets 확인.
3. `Patch`의 자동 schedule이 활성화되어 있는지 확인.
4. 필요하면 `Patch`를 `mode=pre`로 수동 실행해 전체 세 route 호환판을 검증.
5. 번역 정식 배포 시 `Patch`를 `mode=release`로 한 번만 실행.
6. Release 이름이 `v<game>_r<INT_STEAM revision>`이고 세 route manifest가 모두 존재하는지 확인.
7. `distribution/release-index.json`에 세 route entry가 갱신됐는지 확인.
8. Android APK를 새로 배포해야 할 때 `INT_ANDROID APK`를 수동 실행하고 `INT_ANDROID v<game>` Release와 `AstralParty_INT_ANDROID.apk`를 확인.
9. AutoPatcher 버전을 배포할 때 `AutoPatcher`를 수동 실행하고 `AutoPatcher v<version>` Release를 확인.
10. 실기기/MuMu에서 Android installer attribution과 Steam install/update/remove를 검증.
