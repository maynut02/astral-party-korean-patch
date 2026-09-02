# Astral Party Korean Patch

Astral Party 비공식 한국어 패치 프로젝트입니다. 게임 업데이트를 감지해 route별 패치 파일을 생성하고, Windows의 `WindowsPatcher`와 Android의 `AndroidPatcher`로 설치·업데이트합니다.

## 지원 환경

| Route | 플랫폼 | 설치 방식 |
| --- | --- | --- |
| `INT_STEAM` | Windows(Steam) | WindowsPatcher |
| `CN_STEAM` | Windows(Steam) | WindowsPatcher |
| `INT_ANDROID` | Android(JP) | AndroidPatcher + Shizuku |

릴리즈 파일은 GitHub의 [Releases](https://github.com/maynut02/astral-party-korean-patch/releases)에서 배포합니다.

### Android

Android판은 Google Play 서명의 원본 게임을 유지합니다. 게임이 없으면 `AndroidPatcher`가 Actions에서 검증·배포한 무변조 원본 split APK를 받아 Shizuku로 설치할 수 있습니다. 패치 자체는 Shizuku shell 권한으로 외부 Addressables 캐시만 검증·교체하며, 복원용 원본은 동일 게임 버전/revision에 고정된 GitHub Release에서 내려받아 보관합니다. LANG, STR, TMP 폰트 bundle이 패치 대상이며 APK 내부 legacy 폰트는 수정하지 않습니다.

게임을 먼저 실행해 최신 리소스 다운로드를 완료한 뒤 `AndroidPatcher`에서 Shizuku 권한을 허용하고 한글패치를 적용해야 합니다. 게임 업데이트 후에는 새 리소스를 내려받은 다음 패치를 다시 적용합니다.

## 저장소 구성

- `builder/` — 게임 리소스 확인, DB 동기화, 패치 생성·검증
- `windows-patcher/` — Windows용 `WindowsPatcher` 소스
- `android-patcher/` — Android용 `AndroidPatcher` 소스
- `routes/` — route별 게임/번역/리소스 설정
- `resources/` — 패치 생성에 필요한 route별 리소스
- `schemas/` — release/manifest 설정 스키마
- `tools/patch_watcher.py` — 자체 서버에서 게임 revision/hash를 감시하고 변경 시 Patch workflow를 호출하는 경량 watcher
- `.github/workflows/` — CI 및 릴리즈 자동화

## Patch watcher

DB migration은 `astral-patch-site/database/migrations`에서 관리합니다. 해당 migration 적용 후 관리 사이트는 `patch_watch_config.game_version`만 변경하면 됩니다. watcher는 `INT_STEAM`, `CN_STEAM`, `INT_ANDROID`의 현재 revision과 catalog hash를 `patch_watch_routes`에 기록하고, 변경이 있을 때만 `patch.yml`을 `release` 모드로 실행합니다.

```sql
UPDATE patch_watch_config
SET game_version = '3.3.0', updated_at = now()
WHERE singleton = true;
```

운영 서버에서는 이 저장소의 Docker Compose가 watcher 컨테이너를 실행합니다. watcher는 site 저장소의 `astral-patch_default` Docker 네트워크에 참여해 `postgres-db:5432`로 DB에 접근하고, `GITHUB_TOKEN`으로 Patch workflow를 실행합니다. 5분마다 검사하며 동일 변경의 workflow가 아직 DB에 처리되지 않은 경우 30분 뒤 한 번 더 dispatch할 수 있습니다.

`main` push의 CI가 모두 통과하면 기존 DB SSH 접속 정보와 `PATCH_SERVER_WORK_DIR`을 사용해 서버의 이 저장소를 해당 commit으로 전환하고 watcher 이미지만 직접 다시 빌드합니다. site 저장소의 배포 workflow를 경유하지 않습니다.

```bash
docker compose -p astral-patch-watcher up -d --build patch-watcher
docker compose -p astral-patch-watcher logs -f patch-watcher
```

## 개발

Builder는 Python 3.12+, WindowsPatcher는 stable Rust toolchain, AndroidPatcher는 JDK 17/Gradle 9.4.1/Android SDK 36을 사용합니다.
필요한 환경변수와 GitHub Actions Secret/Variable 목록은 [`.env.example`](.env.example)에 정리되어 있습니다.

```bash
python -m pip install -e './builder[dev]'
python -m ruff check builder tools
python -m pytest builder/tests

cd windows-patcher
cargo fmt --all -- --check
cargo test --locked --all-targets --all-features
cargo clippy --locked --all-targets --all-features -- -D warnings
```

## 라이선스

[MIT License](LICENSE)
Astral Party의 상표 및 모든 게임 리소스는 Shanghai Electric Cicada의 소유입니다.
