# Builder

`astral_builder`는 GitHub Actions에서 게임 원본을 탐색하고 Neon DB와 동기화한 뒤 검증 가능한 patch manifest/payload를 생성하는 Python 3.12 도구입니다.

주요 CLI:

```text
astral-builder check
astral-builder sync
astral-builder build
astral-builder validate-build
astral-builder translation-status
astral-builder update-index
astral-builder mark-released
```

## 번역 source 구조

번역 원문은 `INT_STEAM` 하나만 canonical source로 사용합니다.

- `INT_STEAM`: LANG/STR 원문 추출 후 `source_texts` 동기화.
- `CN_STEAM`: catalog/bundle target metadata만 동기화.
- `INT_ANDROID`: catalog/bundle target metadata만 동기화.
- 세 route build: 같은 game version의 최신 processed `INT_STEAM` translation snapshot 사용.

`approved`이면서 현재 source fingerprint와 일치하는 번역만 patch에 들어갑니다. 다른 상태는 원문을 유지하며 `translation-status`가 상세 상태를 보고합니다.

## 로컬 검증

```bash
python -m pip install -e './builder[dev]'
python -m ruff check builder tools
python -m pytest builder/tests
```

운영 workflow와 Release 정책은 [`../docs/OPERATIONS.md`](../docs/OPERATIONS.md)를 참조합니다.
