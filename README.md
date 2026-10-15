# Astral Party Korean Patch

Astral Party 비공식 한국어 패치의 게임 업데이트 감지, 번역 동기화, 패치 생성·검증·배포를 관리합니다. 설치 클라이언트와 원본 Android APK 배포는 `astral-party-auto-patcher` 저장소에서 관리합니다.

## 지원 환경

| Route | 플랫폼 | 설치 방식 |
| --- | --- | --- |
| `INT_STEAM` | Windows(Steam) | WindowsPatcher |
| `CN_STEAM` | Windows(Steam) | WindowsPatcher |
| `INT_ANDROID` | Android(JP) | AndroidPatcher + Shizuku |
| `CN_ANDROID` | Android(CN) | AndroidPatcher + Shizuku |

릴리즈 파일은 GitHub의 [Releases](https://github.com/maynut02/astral-party-korean-patch/releases)에서 배포합니다.

### Steam 폰트 입력

`INT_STEAM`과 `CN_STEAM`은 각 revision에 저장된 Addressables catalog를 기준으로 TMP 폰트의 원본 위치를 결정합니다. CDN 번들은 기존 다운로드 경로를 사용하고, `Addressables.RuntimePath` 번들은 Steam의 `StreamingAssets/aa`에서 가져옵니다. 번들 파일명과 의존 번들은 catalog에서 매번 계산하므로 업데이트마다 다운로드 목록을 수정할 필요가 없습니다.

`Patch` workflow의 `steam-inputs` job은 동기화된 revision ID로 `astral-builder steam-inputs`를 실행해 DepotDownloader 파일 목록을 생성합니다. `data.unity3d`와 필요한 로컬 번들을 같은 route artifact로 전달하고, 빌더는 `--steam-data-dir` 아래에서 파일 크기·번들 이름과 실제 폰트/Atlas 대상을 검증합니다. CDN 원본의 기존 DB 해시 검증과 manifest의 원본 백업 정보는 유지합니다. Android 입력과 빌드 방식은 변경하지 않습니다.

### Android

Android 패치는 Google Play 서명의 원본 게임 APK를 수정하지 않고 외부 Addressables 캐시의 LANG, STR, TMP 폰트 bundle만 대상으로 생성합니다. 실제 설치·복원과 원본 split APK 배포는 `maynut02/astral-party-auto-patcher`의 AndroidPatcher에서 담당합니다.

## 저장소 구성

- `builder/` — 게임 리소스 확인, DB 동기화, 패치 생성·검증
- `routes/` — route별 게임/번역/리소스 설정
- `resources/` — 패치 생성에 필요한 route별 리소스
- `schemas/` — release/manifest 설정 스키마
- `tools/patch_watcher.py` — 자체 서버에서 게임 revision/hash를 감시하고 변경 시 Patch workflow를 호출하는 경량 watcher
- `.github/workflows/` — CI 및 릴리즈 자동화

## Patch watcher

DB migration은 `astral-patch-site/database/migrations`에서 관리합니다. 해당 migration 적용 후 관리 사이트는 `patch_watch_config.game_version`만 변경하면 됩니다. watcher는 `INT_STEAM`, `CN_STEAM`, `INT_ANDROID`, `CN_ANDROID`의 현재 revision과 catalog hash를 `patch_watch_routes`에 기록하고, 변경이 있을 때 `repository_dispatch`로 `patch.yml`을 실행합니다. 새 게임 version/revision의 첫 자동 패치는 `_p0` 정식 Release로 생성됩니다. GitHub Actions에서 Patch workflow를 수동 실행하면 별도의 버전 입력 없이 가장 최근에 배포된 Patch Release의 네 manifest를 기준으로 동일한 game version/revision을 재사용하고 `_p1`, `_p2` 순서로 새 immutable Release를 생성합니다. 따라서 수동 실행은 아직 watcher가 배포하지 않은 원격 revision으로 넘어가지 않습니다. 복원용 원본은 새 game version/revision의 최초 `_p0` 자동 패치에서만 네 route를 하나의 `v<game-version>_r<revision>-original` Release로 생성하며 수동 재빌드는 기존 원본 Release를 그대로 재사용합니다.

```sql
UPDATE patch_watch_config
SET game_version = '3.3.0', updated_at = now()
WHERE singleton = true;
```

운영 서버에서는 이 저장소의 Docker Compose가 watcher 컨테이너를 실행합니다. watcher는 site 저장소의 `astral-patch_default` Docker 네트워크에 참여해 `postgres-db:5432`로 DB에 접근하고, 이 저장소에 `Contents: Read and write` 권한이 있는 `GITHUB_TOKEN`으로 자동 패치 이벤트를 전송합니다. 5분마다 검사하며 동일 변경의 workflow가 아직 DB에 처리되지 않은 경우 30분 뒤 한 번 더 dispatch할 수 있습니다.

`main` push의 CI가 모두 통과하면 기존 DB SSH 접속 정보와 `PATCH_SERVER_WORK_DIR`을 사용해 서버의 이 저장소를 해당 commit으로 전환하고 watcher 이미지만 직접 다시 빌드합니다. site 저장소의 배포 workflow를 경유하지 않습니다.

```bash
docker compose -p astral-patch-watcher up -d --build patch-watcher
docker compose -p astral-patch-watcher logs -f patch-watcher
```

## 개발

Builder는 Python 3.12+를 사용합니다. 필요한 환경변수와 GitHub Actions Secret 목록은 [`.env.example`](.env.example)에 정리되어 있습니다.

```bash
python -m pip install -e './builder[dev]'
python -m ruff check builder tools
python -m pytest builder/tests
```

## 라이선스

[MIT License](LICENSE)
Astral Party의 상표 및 모든 게임 리소스는 Shanghai Electric Cicada의 소유입니다.
