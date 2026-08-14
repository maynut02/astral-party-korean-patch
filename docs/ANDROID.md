# INT_ANDROID 한국어 APK 및 AutoPatcher

INT_ANDROID는 Google Play에서 받은 Astral Party APK를 기반으로 한국어 legacy font와 `AstralPatchRuntime`을 삽입한 APK를 배포합니다. 실제 설치는 `AstralAutoPatcher.exe`가 담당합니다. 사용자는 ADB를 직접 설치하거나 명령을 입력할 필요가 없습니다.

실제 기기에서는 최초 1회 Android 개발자 옵션의 USB 디버깅을 켜고 PC의 RSA 디버깅 승인을 허용해야 합니다. 이후에는 기기를 연결하고 AutoPatcher에서 `Android 패치 / 업데이트`를 선택하면 됩니다. MuMu/BlueStacks/LDPlayer는 실행 중인 인스턴스를 자동 탐색합니다.

## 최종 구조

```text
Google Play APK / split APKs
  -> apkeep로 취득
  -> APKEditor standalone 병합 (원본 Play signature metadata 보존)
  -> 한국어/runtime 수정본 생성
       - assets/bin/Data/data.unity3d의 MochiyPopOne-Regular 교체
       - com.astralpatch.runtime.BootstrapActivity 주입
       - AstralPatchRuntime DEX 추가
  -> zipalign -p
  -> Google Play 원본 APK Signing Block을 수정본에 이식
       - 수정으로 인해 서명 digest는 유효하지 않지만 원본 인증서 정보는 유지
  -> JingMatrix/LSPatch v0.8 sigbypass level 2를 마지막에 적용
       - 이식된 Play signing block에서 원본 인증서를 config에 캡처
       - nested origin.apk/file-link 구조를 한 번만 생성
       - 장기 Android signing key로 최종 서명
  -> AstralParty_INT_Korean.apk
  -> GitHub immutable release
  -> android-apk-index.json 갱신
  -> AstralAutoPatcher가 SHA-256 검증 후 설치
       installerPackageName=com.android.vending
```

Astral Party에는 Google Play installer 검사가 있으며 일반 파일 관리자/앱플레이어의 APK 설치 기능으로 설치하면 Play Store로 이동할 수 있습니다. 실제 로그에서는 `LicenseClient: Local install check failed due to wrong installer.`와 `com.pairip.licensecheck.LicenseActivity`가 확인됐습니다. AutoPatcher는 내부적으로 installer를 `com.android.vending`으로 지정하고 설치 후 `dumpsys package`로 다시 검증합니다.

## AutoPatcher Android 동작

AutoPatcher는 `%LOCALAPPDATA%\AstralAutoPatcher` 아래에 Android 연결 도구와 APK cache를 관리합니다.

1. 내부 Platform-Tools가 없으면 Google의 공식 Windows Platform-Tools를 자동 준비합니다.
2. USB Android 기기를 `adb devices -l`로 찾습니다.
3. MuMu, BlueStacks, LDPlayer의 알려진 ADB executable도 자동 탐색합니다.
4. 패치 가능한 기기가 1대이면 자동 선택합니다.
5. `android-apk-index.json`을 받아 package/game version/download URL/size/SHA-256/installer를 검증합니다.
6. APK를 cache에 다운로드하며 size와 SHA-256을 검증합니다.
7. 기존 한국어판이면 `adb install -r -i com.android.vending`으로 업데이트합니다.
8. Google Play 공식판처럼 다른 서명의 앱이 설치되어 있으면 최초 1회 제거가 필요하므로 앱 데이터 삭제 가능성을 명확히 경고하고 사용자의 확인을 받습니다.
9. 설치 후 `installerPackageName=com.android.vending`인지 확인하고 성공 처리합니다.

기기가 `unauthorized`이면 휴대폰 화면에서 USB 디버깅 RSA 허용을 안내합니다. 여러 기기가 동시에 연결돼 있으면 오설치를 막기 위해 자동 설치하지 않고 사용자에게 한 대만 남기도록 안내합니다.

## Addressables 실행 순서

APK 내부 legacy TTF는 APK 빌드 단계에서만 사용합니다. Addressables 번역/TMP font는 기존 `release-index.json` + patch manifest를 사용해 게임 APK 내부 runtime이 app-specific storage에서 적용합니다.

```text
한국어 APK 실행
  -> BootstrapActivity
  -> 현재 catalog/hash 검사
      -> catalog 없음: 원본 게임 Activity 실행
      -> 호환 patch 없음: 원본 게임 Activity 실행
      -> 필요한 원본 bundle 미완료/불일치: 원본 게임 Activity 실행
      -> sourceSize/sourceSha256 모두 일치: Unity 시작 전에 patch 적용
  -> com.femoo.sdk.Femoo_UnityActivity 실행
```

게임 실행 중에는 Addressables를 수정하지 않습니다. `UpdateWatcher`가 새 catalog 또는 다운로드 완료 상태를 감지하면 완전 종료 후 재실행을 안내하고, 다음 실행의 BootstrapActivity가 patch를 적용합니다.

호환성 기준은 `gameVersion + catalogHash + route + channel`입니다.

## 안전성

- manifest의 `sourceSize`/`sourceSha256` 검증
- transport SHA-256 및 압축 해제 후 payload SHA-256 이중 검증
- `.astralpatch/backups/<catalogHash>/...` 원본 backup
- 적용 실패 시 rollback
- Unity 실행 중 AssetBundle 교체 금지
- AutoPatcher APK download size/SHA-256 검증
- 설치 후 package와 installer 재검증

## GitHub Actions APK 빌드

`.github/workflows/build-android-game-apk.yml`의 `Build Android APK`를 수동 실행합니다.

입력:

- `publish=false`: build artifact만 생성
- `publish=true`: immutable Android APK release를 생성하고 `android-apk-index/android-apk-index.json`을 갱신

Google Play 다운로드 설정:

```text
package: com.feimo.astralpartyjpn
locale: ja_JP
timezone: Asia/Tokyo
split_apk: true
include_additional_files: true
```

고정/관리 도구:

- `apkeep`: `ghcr.io/efforg/apkeep:stable`
- APKEditor: `1.4.9`
- Apktool: `3.0.3`
- JingMatrix/LSPatch: `v0.8`
- JDK: `21`
- Android build-tools: `35.0.0`

필수 Actions Secrets:

- `PLAY_EMAIL`
- `AAS_TOKEN`
- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

Android signing 관련 4개 secret은 앞으로 모든 한국어 APK update에서 같은 값을 유지해야 합니다.

## Android APK index

정식 schema는 `schemas/android-apk-index.schema.json`에 있습니다.


AutoPatcher가 읽는 rolling index는 다음 형식입니다.

```json
{
  "schemaVersion": 1,
  "packageName": "com.feimo.astralpartyjpn",
  "gameVersion": "3.2.0",
  "downloadUrl": "https://github.com/.../releases/download/<immutable-tag>/AstralParty_INT_Korean.apk",
  "sha256": "...",
  "size": 123456789,
  "installerPackageName": "com.android.vending"
}
```

AutoPatcher는 GitHub release base URL 밖의 APK URL이나 다른 package/installer를 거부합니다.

## APK 업데이트와 Addressables 업데이트

APK가 업데이트되면 `Build Android APK`를 `publish=true`로 실행해 새 APK release와 Android index를 발행합니다. AutoPatcher 사용자는 다음 실행에서 새 APK를 자동으로 받아 같은 signing key로 update 설치합니다.

APK가 바뀌지 않고 Addressables만 변경되면 APK를 다시 배포할 필요가 없습니다. 새 INT_ANDROID patch release만 생성하면 설치된 APK의 runtime이 새 catalog를 감지해 다음 실행에 적용합니다.

## 로컬 빌드 주의사항

운영 빌드의 핵심 순서는 `Google Play 병합본 -> build_game_apk.py --prepare-for-lspatch -> LSPatch`입니다. LSPatch 출력은 `assets/lspatch/origin.apk`와 file-link가 겹치는 특수 ZIP 구조라 생성 후 다시 열어 수정하지 않습니다. Builder가 먼저 일반 APK 상태에서 한국어/runtime 변경과 `zipalign -p`를 끝낸 뒤, 원본 Play APK의 v2/v3 APK Signing Block을 그대로 이식합니다. 이 중간 APK의 cryptographic digest는 수정 때문에 유효하지 않지만 LSPatch의 signature helper는 signing block의 인증서 정보를 읽어 원본 signature를 config에 기록합니다. 그 후 LSPatch가 최종 nested APK 구조를 한 번만 생성하고 장기 signing key로 서명합니다.
