# Astral Party Korean Patch

Astral Party 비공식 한국어 패치 프로젝트입니다. 게임 업데이트를 감지해 route별 패치 파일을 생성하고, Windows의 `WindowsPatcher`와 Android의 `AndroidPatcher`로 설치·업데이트합니다.

## 지원 환경

| Route | 플랫폼 | 설치 방식 |
| --- | --- | --- |
| `INT_STEAM` | Windows(Steam) | WindowsPatcher |
| `CN_STEAM` | Windows(Steam) | WindowsPatcher |
| `INT_ANDROID` | Android(JP) | WindowsPatcher(ADB) / AndroidPatcher(Shizuku) |

릴리즈 파일은 GitHub의 [Releases](https://github.com/maynut02/astral-party-korean-patch/releases)에서 배포합니다.

### Android

Android판은 Windows의 `WindowsPatcher` 또는 Android의 `AndroidPatcher`를 통해 설치할 수 있습니다. 최초 실행이나 게임 리소스 업데이트 후 한국어 패치를 적용할 준비가 되면 게임 안에 종료 안내가 표시되며, 게임을 종료한 뒤 다시 실행하면 패치가 자동으로 적용됩니다.

`android-patcher/`에는 Android 11 이상용 `AndroidPatcher` 클라이언트가 있습니다. Shizuku가 없으면 `thedjchi/Shizuku`의 최신 안정 Release를 자동으로 조회하고, 게임 APK는 `distribution/android-apk-index.json`의 크기와 SHA-256을 검증한 뒤 Shizuku shell 권한으로 설치합니다.

## 저장소 구성

- `builder/` — 게임 리소스 확인, DB 동기화, 패치 생성·검증
- `windows-patcher/` — Windows용 `WindowsPatcher` 소스
- `android/runtime/` — Android APK에 포함되는 런타임 패처
- `android-patcher/` — Android용 `AndroidPatcher` 소스
- `database/` — PostgreSQL migration
- `routes/` — route별 게임/번역/리소스 설정
- `resources/` — 패치 생성에 필요한 route별 리소스
- `schemas/` — release/manifest 설정 스키마
- `tools/patch_watcher.py` — 자체 서버에서 게임 revision/hash를 감시하고 변경 시 Patch workflow를 호출하는 경량 watcher
- `.github/workflows/` — CI 및 릴리즈 자동화

## Patch watcher

`database/migrations/0005_patch_watcher.sql` 적용 후 관리 사이트는 `patch_watch_config.game_version`만 변경하면 됩니다. watcher는 `INT_STEAM`, `CN_STEAM`, `INT_ANDROID`의 현재 revision과 catalog hash를 `patch_watch_routes`에 기록하고, 변경이 있을 때만 `patch.yml`을 `release` 모드로 실행합니다.

```sql
UPDATE patch_watch_config
SET game_version = '3.3.0', updated_at = now()
WHERE singleton = true;
```

서버에서는 `DATABASE_URL`과 repository Actions 실행 권한이 있는 `GITHUB_TOKEN`만 필요합니다. Builder 전체를 설치할 필요 없이 아래처럼 PostgreSQL 드라이버 하나만 설치하고 watcher를 5분 주기로 실행하면 됩니다. 동일 변경의 workflow가 아직 DB에 처리되지 않은 경우 30분 뒤 한 번 더 dispatch할 수 있습니다.

```bash
python -m pip install -r tools/patch_watcher_requirements.txt
python tools/patch_watcher.py
```

## 개발

Builder는 Python 3.12+, WindowsPatcher는 stable Rust toolchain을 사용합니다.
필요한 환경변수와 GitHub Actions Secret/Variable 목록은 [`.env.example`](.env.example)에 정리되어 있습니다.

```bash
python -m pip install -e './builder[dev]'
python -m ruff check builder tools database
python -m pytest builder/tests

cd windows-patcher
cargo fmt --all -- --check
cargo test --locked --all-targets --all-features
cargo clippy --locked --all-targets --all-features -- -D warnings
```

## 라이선스

[MIT License](LICENSE)
Astral Party의 상표 및 모든 게임 리소스는 Shanghai Electric Cicada의 소유입니다.
