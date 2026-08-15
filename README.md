# Astral Party Korean Patch

Astral Party 비공식 한국어 패치 프로젝트입니다. 게임 업데이트를 감지해 route별 패치 파일을 생성하고, `AstralAutoPatcher`로 설치·업데이트합니다.

## 지원 환경

| Route | 플랫폼 | 설치 방식 |
| --- | --- | --- |
| `INT_STEAM` | Windows / Steam | AstralAutoPatcher |
| `CN_STEAM` | Windows / Steam | AstralAutoPatcher |
| `INT_ANDROID` | Android | AstralAutoPatcher + ADB |

릴리즈 파일은 GitHub의 [Releases](https://github.com/maynut02/astral-party-korean-patch/releases)에서 배포합니다. 일반 사용자는 저장소를 직접 빌드할 필요가 없습니다.

### Android

Android판은 `AstralAutoPatcher`를 통해 설치하는 것을 전제로 합니다. 최초 실행이나 게임 리소스 업데이트 후 한국어 패치를 적용할 준비가 되면 게임 안에 종료 안내가 표시되며, 게임을 종료한 뒤 다시 실행하면 패치가 자동으로 적용됩니다.

## 저장소 구성

- `builder/` — 게임 리소스 확인, DB 동기화, 패치 생성·검증
- `patcher/` — Windows용 `AstralAutoPatcher`
- `android/runtime/` — Android APK에 포함되는 런타임 패처
- `database/` — PostgreSQL migration
- `routes/` — route별 게임/번역/리소스 설정
- `resources/` — 패치 생성에 필요한 route별 리소스
- `schemas/` — release/manifest 설정 스키마
- `.github/workflows/` — CI 및 릴리즈 자동화

## 개발

Builder는 Python 3.12+, AutoPatcher는 stable Rust toolchain을 사용합니다.
필요한 환경변수와 GitHub Actions Secret/Variable 목록은 [`.env.example`](.env.example)에 정리되어 있습니다. 저장소는 `.env`를 자동으로 로드하지 않으므로 로컬 실행 시 필요한 값을 환경변수로 설정하세요.

```bash
python -m pip install -e './builder[dev]'
python -m ruff check builder tools database
python -m pytest builder/tests

cd patcher
cargo fmt --all -- --check
cargo test --locked --all-targets --all-features
cargo clippy --locked --all-targets --all-features -- -D warnings
```

## 라이선스

직접 작성한 소스 코드는 [MIT License](LICENSE)로 배포합니다.

`resources/`의 폰트·아틀라스 등 제3자 자산과 Astral Party 자체의 게임 데이터·상표·저작물은 MIT License 적용 대상이 아니며 각 권리자의 조건을 따릅니다. 자세한 내용은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 확인하세요.

이 프로젝트는 Astral Party의 개발사·배급사와 공식적으로 제휴된 프로젝트가 아닙니다.
