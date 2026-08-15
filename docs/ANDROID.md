# INT_ANDROID 한국어 APK 및 AutoPatcher

INT_ANDROID는 Google Play에서 받은 Astral Party APK를 기반으로 한국어 legacy font와 `AstralPatchRuntime`을 삽입한 APK를 배포합니다. 실제 설치는 `AstralAutoPatcher.exe`가 담당하므로 사용자가 ADB를 직접 설치하거나 명령을 입력할 필요가 없습니다.

실제 기기는 최초 1회 Android 개발자 옵션의 USB 디버깅을 켜고 PC의 RSA 디버깅 승인을 허용해야 합니다. 이후에는 기기를 연결하고 AutoPatcher의 Android 패치/업데이트 기능을 사용합니다. MuMu/BlueStacks/LDPlayer는 실행 중인 ADB endpoint를 자동 탐색합니다.

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
       - 수정 때문에 cryptographic digest는 유효하지 않지만 원본 인증서 정보는 유지
  -> JingMatrix/LSPatch v0.8 sigbypass level 2를 마지막에 적용
       - 이식된 Play signing block에서 원본 인증서를 config에 캡처
       - nested origin.apk/file-link 구조를 한 번만 생성
       - 장기 Android signing key로 최종 서명
  -> apksigner verify
  -> AstralParty_INT_ANDROID.apk
  -> GitHub Release: INT_ANDROID v<game-version>
  -> distribution/android-apk-index.json 갱신
  -> AstralAutoPatcher가 SHA-256 검증 후 설치
       installerPackageName=com.android.vending
```

Astral Party에는 Google Play installer 검사가 있으며 일반 파일 관리자/일부 앱플레이어 APK 설치 기능으로 설치하면 Play Store로 이동할 수 있습니다. 실제 로그에서는 `LicenseClient: Local install check failed due to wrong installer.`와 `com.pairip.licensecheck.LicenseActivity`가 확인됐습니다. AutoPatcher는 installer를 `com.android.vending`으로 지정하고 설치 뒤 `dumpsys package` 결과를 다시 검증합니다.

## AutoPatcher Android 동작

AutoPatcher는 `%LOCALAPPDATA%\AstralAutoPatcher` 아래에 Android 연결 도구와 APK cache를 관리합니다.

1. 내부 Platform-Tools가 없으면 Google의 Windows Platform-Tools를 자동 준비합니다.
2. USB Android 기기를 `adb devices -l`로 찾습니다.
3. MuMu, BlueStacks, LDPlayer의 알려진 ADB executable/endpoint도 탐색합니다.
4. 패치 가능한 기기가 한 대이면 자동 선택합니다.
5. `android-apk-index.json`에서 package/game version/download URL/size/SHA-256/installer를 검증합니다.
6. APK를 cache에 다운로드하고 size/SHA-256을 검증합니다.
7. 기존 한국어판이면 `adb install -r -i com.android.vending` 방식으로 업데이트합니다.
8. Google Play 공식판처럼 다른 서명의 앱이 설치되어 있으면 제거 시 데이터가 삭제될 수 있음을 알리고 명시적 확인을 받습니다.
9. 설치 후 package/version/`installerPackageName=com.android.vending`을 다시 검증합니다.

기기가 `unauthorized`이면 휴대폰 화면의 USB 디버깅 RSA 허용이 필요합니다. 여러 기기가 동시에 연결돼 있으면 오설치를 막기 위해 자동 설치하지 않습니다.

## Addressables 실행 순서

APK 내부 legacy TTF는 APK 빌드 단계에서만 교체합니다. LANG/STR/TMP Addressables 패치는 APK에 고정하지 않고 공통 `release-index.json`과 route manifest를 사용합니다.

새 APK에 내장되는 기본 index URL:

```text
https://raw.githubusercontent.com/<owner>/<repo>/distribution/release-index.json
```

```text
한국어 APK 실행
  -> BootstrapActivity
  -> 현재 catalog/hash 검사
      -> catalog 없음: 원본 게임 Activity 실행
      -> 호환 release 없음: 원본 게임 Activity 실행
      -> 필요한 AssetBundle cache entry 미완료: 원본 게임 Activity 실행
      -> BundleName/Hash cache entry 준비 완료: Unity 시작 전에 patch 적용
  -> com.femoo.sdk.Femoo_UnityActivity 실행
```

게임 실행 중에는 Addressables를 수정하지 않습니다. `UpdateWatcher`가 새 catalog 또는 다운로드 완료 상태를 감지하면 완전 종료 후 재실행을 안내하고, 다음 실행의 `BootstrapActivity`가 patch를 적용합니다.

Android에서는 catalog와 AssetBundle cache를 구분합니다. catalog/hash는 `files/com.unity.addressables`에서 읽고 실제 원격 AssetBundle은 Unity cache인 `files/UnityCache/Shared/<BundleName>/<Hash>/__data`에서 찾습니다. Unity가 다운로드한 AssetBundle을 cache에 LZ4로 재압축할 수 있으므로 runtime은 CDN 원본 `sourceSha256/sourceSize`와 cache 파일의 바이트 일치를 다운로드 완료 조건으로 사용하지 않습니다. 대신 현재 catalog가 지정한 BundleName/Hash cache entry가 존재하는지 확인하고, 패치 직전 기기상의 실제 cache 파일을 별도로 백업합니다.

호환성 기준은 `gameVersion + catalogHash + route + channel`입니다. 일반 APK runtime은 `release` channel을 사용합니다.

## 안전성

- `gameVersion + catalogHash`와 manifest의 BundleName/Hash cache path 호환성 확인
- gzip transport SHA-256 및 압축 해제 후 payload SHA-256 이중 검증
- `.astralpatch/backups/<catalogHash>/...`에 기기상의 실제 cache bundle을 SHA-256 확인 후 backup
- 적용 실패 시 rollback
- Unity 실행 중 AssetBundle 교체 금지
- AutoPatcher APK download size/SHA-256 검증
- 설치 후 package/version/installer 재검증

## GitHub Actions APK 빌드

Actions의 `INT_ANDROID APK` workflow를 수동 실행합니다. 별도 `publish` 입력은 없으며 수동 실행은 검증된 APK를 Release하는 운영 작업입니다. 같은 game version의 Release가 이미 있으면 새 APK의 전체 검증이 끝난 뒤 같은 이름/tag로 교체합니다.

Release 형식:

```text
INT_ANDROID v3.2.0
└─ AstralParty_INT_ANDROID.apk
```

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

Android signing 관련 네 secret은 이후 모든 한국어 APK update에서 같은 값을 유지해야 합니다.

## Android APK index

schema는 `schemas/android-apk-index.schema.json`에 있습니다. 새 AutoPatcher가 읽는 endpoint는 다음과 같습니다.

```text
https://raw.githubusercontent.com/<owner>/<repo>/distribution/android-apk-index.json
```

형식:

```json
{
  "schemaVersion": 1,
  "packageName": "com.feimo.astralpartyjpn",
  "gameVersion": "3.2.0",
  "downloadUrl": "https://github.com/.../releases/download/<tag>/AstralParty_INT_ANDROID.apk",
  "sha256": "...",
  "size": 123456789,
  "installerPackageName": "com.android.vending"
}
```

AutoPatcher는 GitHub Release base URL 밖의 APK URL이나 다른 package/installer를 거부합니다.

이전에 배포한 AutoPatcher는 `android-apk-index`라는 legacy machine Release를 참조하므로 해당 Release가 이미 존재하면 workflow가 같은 JSON을 호환용으로 mirror합니다. 새 workflow는 legacy index Release를 새로 만들지 않습니다.

## APK 업데이트와 Addressables 업데이트

게임 APK 자체를 새로 배포해야 할 때 `INT_ANDROID APK`를 수동 실행합니다. 같은 signing key로 만든 이후 한국어판은 update install이 가능합니다.

APK가 바뀌지 않고 Addressables만 변경되면 APK를 다시 배포할 필요가 없습니다. `Patch` workflow의 새 정식 Release에 `INT_ANDROID_manifest.json`이 포함되면 설치된 APK runtime이 해당 catalog용 patch를 사용합니다.

## 로컬 빌드 주의사항

운영 빌드의 핵심 순서는 `Google Play 병합본 -> build_game_apk.py --prepare-for-lspatch -> LSPatch`입니다. LSPatch 출력은 `assets/lspatch/origin.apk`와 file-link가 겹치는 특수 ZIP 구조이므로 생성 후 다시 열어 수정하지 않습니다.

Builder가 먼저 일반 APK 상태에서 한국어/runtime 변경과 `zipalign -p`를 끝낸 뒤 원본 Play APK의 v2/v3 APK Signing Block을 그대로 이식합니다. 이 중간 APK의 cryptographic digest는 수정 때문에 유효하지 않지만 LSPatch signature helper는 signing block의 인증서 정보를 읽어 원본 signature를 config에 기록합니다. 이후 LSPatch가 최종 nested APK 구조를 한 번만 생성하고 장기 signing key로 서명합니다.
