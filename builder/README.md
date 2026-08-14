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

## 원문과 번역 모델

번역 원문은 `INT_STEAM`만 canonical source로 사용합니다.

- `INT_STEAM`: LANG/STR을 추출해 현재 원문과 비교하고 **추가/변경/삭제된 항목만** DB에 기록합니다.
- 하나의 `INT_STEAM game_revisions` row가 해당 업데이트의 원문 변경 그룹입니다.
- 변경되지 않은 원문은 revision마다 복제하지 않습니다.
- `CN_STEAM` / `INT_ANDROID`: route별 catalog/bundle 호환 metadata만 동기화합니다.
- 세 route build는 같은 현재 `INT_STEAM` 원문/승인 번역을 사용합니다.

`translations`에는 승인된 production 번역만 존재합니다. 수정 제안은 먼저 `translation_changes(status=pending)`에 기록되고 승인 transaction이 성공한 경우에만 `translations`에 반영됩니다. 따라서 patch builder는 별도 상태 판정 없이 다음 규칙만 사용합니다.

```text
승인 번역 있음 -> translations.text
승인 번역 없음 -> 게임 원문
```

원문이 바뀌어 새 번역 제안이 대기 중이어도 기존 승인 번역은 계속 production 값으로 유지됩니다.

## 로컬 검증

```bash
python -m pip install -e './builder[dev]'
python -m ruff check builder tools database
python -m pytest builder/tests
```

DB 구조는 [`../database/README.md`](../database/README.md), 운영 workflow는 [`../docs/OPERATIONS.md`](../docs/OPERATIONS.md), 전체 초기화 절차는 [`../docs/RESET.md`](../docs/RESET.md)를 참조합니다.
