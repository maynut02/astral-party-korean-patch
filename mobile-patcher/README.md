# Astral Mobile Patcher

Android 기기에서 INT_ANDROID 한국어판과 Shizuku를 내려받고 설치 준비를 하는 모바일 클라이언트입니다.

현재 구현된 흐름은 다음과 같습니다.

1. `thedjchi/Shizuku`의 GitHub `releases/latest` API에서 최신 안정 APK를 찾습니다.
2. Shizuku APK의 크기, GitHub asset SHA-256, package name을 검증합니다.
3. Android 시스템 패키지 설치 화면을 엽니다.
4. Shizuku가 실행되면 Binder 상태를 확인하고 Mobile Patcher 권한을 요청합니다.
5. `distribution/android-apk-index.json`에서 최신 게임 APK 정보를 가져옵니다.
6. 게임 APK의 크기, SHA-256, package name을 검증하고 시스템 설치 화면을 엽니다.

## 요구 사항

- Android 11 이상
- 인터넷 연결
- Shizuku 사용 시 Shizuku 서비스 실행 및 권한 허용

## 빌드

JDK 17, Android SDK 36, Gradle 8.13이 필요합니다.

```bash
gradle :app:assembleDebug
```

게임 설치의 `installerPackageName=com.android.vending` 강제 설정은 이 앱에 포함되어 있지 않습니다. 해당 기능은 현재 Windows AutoPatcher의 ADB 설치 경로에만 남아 있습니다.

GitHub Actions의 `Mobile Patcher` workflow는 기존 Android APK와 동일한 `ANDROID_KEYSTORE_*` secrets로 release APK를 서명하고 `mobile-patcher-v<version>` Release를 생성합니다. Workflow 실행 시 `patch`, `minor`, `major` 중 하나를 선택하면 기존 `mobile-patcher-v*` Release 중 가장 높은 SemVer를 기준으로 다음 버전을 자동 계산합니다. 기존 Release가 하나도 없으면 최초 버전은 `0.1.0`입니다.
