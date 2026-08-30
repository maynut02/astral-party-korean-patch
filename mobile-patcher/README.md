# Astral Mobile Patcher

Android 기기에서 INT_ANDROID 한국어판과 Shizuku를 내려받고 설치 준비를 하는 모바일 클라이언트입니다.

현재 구현된 흐름은 다음과 같습니다.

1. `thedjchi/Shizuku`의 GitHub `releases/latest` API에서 최신 안정 APK를 찾습니다.
2. Shizuku APK의 크기, GitHub asset SHA-256, package name을 검증합니다.
3. Android 시스템 패키지 설치 화면을 엽니다.
4. Shizuku가 실행되면 Binder 상태를 확인하고 Mobile Patcher 권한을 요청합니다.
5. `distribution/android-apk-index.json`에서 최신 게임 APK 정보를 가져옵니다.
6. 게임 APK의 크기, SHA-256, package name을 검증합니다.
7. Shizuku UserService를 shell 권한으로 실행하고 APK를 `pm install -r -i com.android.vending`에 스트리밍해 설치합니다.
8. 설치 후 Android `InstallSourceInfo`를 다시 조회해 실제 설치 출처가 `com.android.vending`인지 검증합니다.

## 요구 사항

- Android 11 이상
- 인터넷 연결
- Shizuku 사용 시 Shizuku 서비스 실행 및 권한 허용

## 빌드

JDK 17, Android SDK 36, Gradle 8.13이 필요합니다.

```bash
gradle :app:assembleDebug
```

Shizuku 자체는 Shizuku가 아직 실행될 수 없는 초기 부트스트랩 단계이므로 Android 시스템 설치 화면을 사용합니다. 게임 APK는 시스템 설치 화면을 사용하지 않으며, Windows AutoPatcher의 `adb install -r -i com.android.vending`과 같은 설치 출처를 남기도록 Shizuku shell 권한으로 설치합니다.

GitHub Actions의 `Mobile Patcher` workflow는 기존 Android APK와 동일한 `ANDROID_KEYSTORE_*` secrets로 release APK를 서명하고 `mobile-patcher-v<version>` Release를 생성합니다. Workflow 실행 시 `patch`, `minor`, `major` 중 하나를 선택하면 기존 `mobile-patcher-v*` Release 중 가장 높은 SemVer를 기준으로 다음 버전을 자동 계산합니다. 기존 Release가 하나도 없으면 최초 버전은 `0.1.0`입니다.

Release가 발행되면 workflow가 `distribution/mobile-patcher-index.json`도 함께 갱신합니다. Mobile Patcher는 앱 시작과 새로고침 시 이 인덱스를 확인하고 현재 `versionCode`보다 새 버전이 있으면 업데이트 패널을 표시합니다. 업데이트 APK는 고정된 GitHub Release URL, SHA-256, 파일 크기, packageName, versionName, versionCode와 현재 앱의 서명 인증서를 검증한 뒤 Android 시스템 설치 화면으로 전달합니다. 업데이트 확인 실패는 Shizuku 및 게임 설치 기능을 차단하지 않습니다.
