# INT_ANDROID 한국어 APK 및 내장 패처

INT_ANDROID는 Windows AstralAutoPatcher/ADB를 사용하지 않습니다. Android 게임 APK 자체에 한국어 legacy TTF와 `AstralPatchRuntime`을 넣고, 같은 signing key로 계속 재서명해 배포합니다. Addressables 번역/TMP 폰트는 기존 `release-index.json` + patch manifest를 사용해 게임 APK 내부 런타임이 자기 app-specific storage에서 직접 적용합니다.

## 구성

```text
Google Play APK / split APKs
  -> apkeep로 취득
  -> APKEditor로 standalone APK 병합
  -> assets/bin/Data/data.unity3d의 MochiyPopOne-Regular 교체
  -> com.astralpatch.runtime.BootstrapActivity 주입
  -> AstralPatchRuntime DEX 추가
  -> 고정 signing key로 재서명
  -> 한국어 게임 APK

INT_ANDROID patch release
  -> Japanese LANG bundle
  -> GameData_INT / STR bundle
  -> MochiyPopOne-Regular_TMP bundle
  -> manifest.json (sourceSha256/sourceSize 포함)
```

APK 내부 legacy TTF는 patch release asset으로 배포하지 않습니다. `resources/int_android/legacy-font.ttf`는 APK 빌드 단계에서만 사용합니다.

## Addressables 실행 순서

게임 자체의 인게임 리소스 다운로드가 항상 우선입니다.

```text
한국어 APK 실행
  -> BootstrapActivity
  -> 현재 catalog/hash 검사
      -> catalog 없음: 원본 게임 Activity 실행
      -> 호환 patch 없음: 원본 게임 Activity 실행
      -> 필요한 원본 bundle이 아직 없음/불일치: 원본 게임 Activity 실행
      -> manifest의 모든 sourceSize/sourceSha256 일치: 게임 Activity 시작 전에 patch 적용
  -> com.femoo.sdk.Femoo_UnityActivity 실행
```

게임 실행 중에는 Addressables를 수정하지 않습니다. `UpdateWatcher`가 catalog 변경 또는 아직 준비되지 않았던 현재 catalog를 주기적으로 확인하고, 호환 manifest의 모든 원본 파일 SHA-256/size가 맞아 인게임 다운로드 완료가 확인되면 사용자에게 게임을 완전히 종료한 뒤 다시 실행하라는 안내를 표시합니다. 다음 실행의 BootstrapActivity가 Unity 시작 전에 패치를 적용합니다.

이 흐름은 다음 두 경우를 같은 방식으로 처리합니다.

- APK 최초 설치 후 첫 인게임 리소스 다운로드
- APK version은 그대로인데 게임 서버가 새 catalog/AssetBundle만 배포한 경우

패치 호환성 기준은 APK version 자체가 아니라 `gameVersion + catalogHash + route + channel`입니다.

## 안전성

Addressables 적용 전에는 manifest에 기록된 `sourceSize`와 `sourceSha256`을 모두 확인합니다. 모든 patch payload는 staging에서 다운로드 SHA-256, 압축 해제 후 payload SHA-256을 검증한 뒤 적용합니다.

실제 파일을 바꾸기 전에 현재 원본을 `.astralpatch/backups/<catalogHash>/...`에 백업합니다. 적용 중 실패하면 백업에서 원본을 복구합니다. 중간 종료 흔적이 남아 있으면 다음 실행에서 완전한 백업 세트가 있는 경우 원본으로 되돌린 뒤 다시 검증합니다.

게임 실행 중에는 파일을 교체하지 않으므로 Unity가 이미 연 AssetBundle과 충돌하지 않습니다.

## INT_ANDROID patch 자동화

`Check Game`은 INT_STEAM을 먼저 처리한 뒤 INT_ANDROID를 처리합니다. INT_ANDROID는 `canonicalFallbackRoute: INT_STEAM`을 사용하며 Steam legacy data 취득 단계는 없습니다.

Android patch manifest에는 Addressables 3종만 들어갑니다.

- `Japanese` LANG
- `GameData_INT` STR
- `MochiyPopOne-Regular_TMP`

각 Addressables entry에는 patched payload 정보 외에 `sourceSha256`과 `sourceSize`가 추가됩니다. Android runtime은 이 값으로 게임 자체 다운로드 완료 여부를 판단합니다.

## 한국어 게임 APK 로컬 빌드

필요 도구:

- Python 3.12 + `builder[dev]`
- JDK 17
- Android SDK platform/build-tools (`d8`, `zipalign`, `apksigner`)
- `apktool`
- 현재 게임 버전의 merged standalone APK (로컬 빌드 시 직접 준비)
- 장기간 유지할 Android signing keystore

비밀번호는 명령행 값으로 직접 넘기지 않고 환경변수로 전달합니다.

```bash
export ANDROID_SDK_ROOT=/path/to/android-sdk
export ASTRAL_ANDROID_KEYSTORE_PASSWORD='...'
export ASTRAL_ANDROID_KEY_PASSWORD='...'

python tools/android/build_game_apk.py \
  --base-apk '.int_android/current-merged.apk' \
  --output 'output/android/AstralParty_INT_Korean.apk' \
  --release-index-url 'https://github.com/<owner>/<repo>/releases/download/patch-index/release-index.json' \
  --keystore '.secrets/android-signing.keystore' \
  --key-alias 'astralparty'
```

빌드 스크립트는 다음을 수행합니다.

1. Apktool 3의 `decode --no-src --no-assets`로 manifest/resources 전용 임시 프로젝트를 만들고 `AndroidManifest.xml`만 수정·재컴파일합니다.
2. 기존 MAIN/LAUNCHER intent를 `com.femoo.sdk.Femoo_UnityActivity`에서 제거하고 `com.astralpatch.runtime.BootstrapActivity`에 부여합니다.
3. 원본 APK ZIP에서 `assets/bin/Data/data.unity3d`만 추출해 `MochiyPopOne-Regular`를 `resources/int_android/legacy-font.ttf`로 교체하고 UnityPy 재로드 검증을 수행합니다.
4. Android runtime Java 소스를 `javac` + `d8`로 별도 DEX로 빌드합니다.
5. 원본 APK를 전체 rebuild하지 않고 기존 ZIP entry를 스트리밍 복사하면서 binary manifest, `data.unity3d`, 새 `classesN.dex`, `assets/astralpatch/config.json`만 교체/추가합니다.
6. 기존 서명 entry를 제거한 뒤 `zipalign`하고 지정된 고정 keystore로 `apksigner` 서명/검증합니다.

## GitHub Actions APK 빌드

`.github/workflows/build-android-game-apk.yml`을 수동 실행합니다. workflow 입력값은 없으며 원본 APK URL도 입력하지 않습니다. Actions가 `apkeep`으로 Google Play에서 `com.feimo.astralpartyjpn`의 현재 APK/split APK를 직접 내려받고, split이 여러 개면 APKEditor로 단일 APK로 병합한 뒤 한국어 APK를 빌드합니다.

Google Play 다운로드 설정은 다음 값으로 고정합니다.

```text
package: com.feimo.astralpartyjpn
locale: ja_JP
timezone: Asia/Tokyo
split_apk: true
include_additional_files: true
```

빌드 도구는 다음 버전/채널을 사용합니다. `apkeep`은 upstream이 권장하는 `stable` floating tag를 사용하고, 나머지 빌드 도구는 고정 버전을 사용합니다.

- `apkeep` Docker image: `ghcr.io/efforg/apkeep:stable`
- APKEditor: `1.4.9`
- Apktool: `3.0.3`
- Android build-tools: `35.0.0`

`apkeep`이 split APK 여러 개를 반환하면 APKEditor `merge`를 사용합니다. APK가 하나뿐이면 그대로 Builder 입력으로 사용합니다. Google Play가 OBB expansion file을 반환하면 현재 flow에서는 조용히 무시하지 않고 빌드를 실패시킵니다. 이 경우 OBB 지원을 별도로 추가해야 합니다.

필수 Actions Secrets:

- `PLAY_EMAIL`
- `AAS_TOKEN`
- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

`PLAY_EMAIL`과 `AAS_TOKEN`은 Google Play 다운로드 전용입니다. Android signing 관련 4개 secret은 우리가 배포하는 한국어 APK의 고정 signing identity입니다.

Google Play 앱이 지역 제한되어 있으면 locale/timezone 설정만으로는 우회되지 않습니다. `PLAY_EMAIL` 계정이 해당 앱을 지원 지역에서 한 번 이상 취득해 계정에 연결된 상태여야 합니다. `apkeep`이 `Invalid app response`로 다운로드를 건너뛰면 workflow는 즉시 실패하며 이 조건을 안내합니다.

workflow는 `output/android/AstralParty_INT_Korean.apk`를 만들고 결과를 `astral-party-int-android-korean-apk` Actions artifact로 업로드합니다. 같은 게임 패키지에 업데이트 설치하려면 이후 모든 한국어 APK 빌드에서 같은 signing key를 계속 사용해야 합니다.

## APK 업데이트와 Addressables 업데이트

APK가 업데이트되면 `Build Android APK` workflow가 Google Play에서 새 APK/split APK를 다시 취득하고 병합한 뒤 한국어 APK를 빌드합니다. 이때 legacy TTF와 runtime을 새 APK에 다시 삽입합니다.

APK 업데이트가 없고 Addressables만 바뀌면 APK를 다시 빌드할 필요가 없습니다. Builder가 새 INT_ANDROID revision/catalog용 patch release를 만들고, 이미 설치된 한국어 APK의 runtime이 새 catalog를 감지해 다음 실행에 새 Addressables 패치를 적용합니다.
