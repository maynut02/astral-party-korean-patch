# AndroidPatcher

Google Play 원본 Astral Party를 유지하면서 외부 Addressables 캐시에 한국어 패치를 적용하는 Android 11 이상용 클라이언트입니다. UI는 Jetpack Compose와 Material Design 3를 사용합니다.

## 동작 방식

1. `com.feimo.astralpartyjpn`이 Google Play에서 설치되었는지 확인합니다. 예전 변조 APK는 패치 대상으로 인정하지 않습니다.
2. Shizuku 연결과 AndroidPatcher 권한을 확인합니다.
3. Shizuku UserService가 게임의 `com.unity.addressables` catalog version/hash를 읽습니다.
4. 고정된 `release-index.json`에서 정확히 일치하는 `INT_ANDROID` 정식 패치를 찾습니다.
5. 검증된 manifest에 기록된 모든 patch 대상 원본 파일의 존재 여부, 크기, SHA-256을 먼저 확인합니다.
6. 사용자가 적용을 시작하면 모든 gzip/payload의 크기와 SHA-256을 검증합니다.
7. 게임을 종료하고 기존 cache 파일을 영구 backup과 transaction backup에 저장합니다.
8. LANG, STR, TMP 폰트 bundle을 원자적으로 교체합니다.
9. 중간 실패 시 transaction backup으로 자동 rollback합니다.
10. 사용자는 앱에서 원본 파일을 복원할 수 있습니다.

게임 APK 다운로드, APK 재서명, LSPatch, `pm install`, 설치 출처 위장은 사용하지 않습니다. APK 내부 `assets/bin/Data/data.unity3d`의 legacy 폰트는 원본 상태로 유지합니다.

## 요구 사항

- Android 11 이상
- Google Play에서 설치한 원본 게임
- 게임 최초 실행과 최신 리소스 다운로드 완료
- Shizuku 설치·서비스 시작·AndroidPatcher 권한 허용
- 인터넷 연결

일반 Android 앱은 다른 앱의 app-specific external directory에 접근할 수 없으므로 Shizuku shell 권한이 필요합니다. 제조사와 Android 버전에 따라 shell의 `/storage/emulated/0/Android/data/...` 접근 동작이 다를 수 있어 실제 기기 검증이 필요합니다.

Shizuku가 설치되지 않았으면 앱이 기존 방식대로 GitHub 최신 안정 APK를 내려받아 SHA-256과 packageName을 검증한 뒤 Android 시스템 설치 화면을 엽니다. Android 보안 정책상 사용자의 설치 확인은 생략하지 않습니다.

앱은 `mobile-patcher-index.json`에서 새 AndroidPatcher 릴리스를 확인합니다. 새 버전이 있으면 APK의 크기, SHA-256, packageName, versionName, versionCode와 현재 앱의 서명을 모두 검증한 뒤 시스템 업데이트 설치 화면을 엽니다.

## 보안 경계

- patch 대상 package와 파일 root는 코드에 고정되어 있습니다.
- Shizuku 서비스는 임의 shell command API를 노출하지 않습니다.
- manifest의 `game-data` target과 안전하지 않은 상대 경로를 거부합니다.
- index, manifest, payload URL은 프로젝트의 HTTPS GitHub 경로로 제한합니다.
- Shizuku APK는 고정된 GitHub repository의 안정 release만 허용하고 release digest와 packageName을 검증합니다.
- AndroidPatcher 자체 업데이트는 고정된 distribution index와 release 경로만 허용하고 현재 앱과 동일한 서명인지 확인합니다.
- payload는 앱 프로세스와 shell 서비스 양쪽에서 SHA-256/크기를 검증합니다.
- patch 시작 전에 게임을 강제 종료합니다.
- crash가 남긴 transaction은 다음 진단 시 원래 파일로 복구합니다.

## 빌드

JDK 17, Android SDK 36, Gradle 9.4.1이 필요합니다.

```bash
gradle :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

GitHub Actions의 `AndroidPatcher` workflow는 `ANDROID_KEYSTORE_*` secrets로 release APK를 서명하고 `android-patcher-v<version>` immutable Release를 생성합니다. 배포 파일은 `AstralAndroidPatcher.apk`입니다.
