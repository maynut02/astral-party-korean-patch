from astral_builder.database.snapshot import (
    SnapshotUnit,
    TranslationState,
    make_snapshot,
)
from astral_builder.formats.model import SourceStrings


def _unit(
    key: str,
    *,
    translation: str = "",
    source_fp: str = "a" * 64,
    translation_fp: str | None = None,
    status: str | None = None,
) -> SnapshotUnit:
    return SnapshotUnit(
        kind="lang",
        namespace="lang",
        key=key,
        source=SourceStrings(en=f"source-{key}"),
        source_fingerprint=source_fp,
        translation=translation,
        translation_status=status,
        translation_source_fingerprint=translation_fp,
    )


def test_snapshot_computes_translation_state_without_storing_derived_status() -> None:
    assert _unit("A").state is TranslationState.UNTRANSLATED
    changed = _unit("A", translation="번역", translation_fp="b" * 64)
    assert changed.state is TranslationState.NEEDS_REVIEW
    assert (
        _unit(
            "A",
            translation="번역",
            translation_fp="a" * 64,
            status="approved",
        ).state
        is TranslationState.APPROVED
    )


def test_snapshot_fingerprint_is_order_independent_and_content_sensitive() -> None:
    a = _unit("A", translation="하나", translation_fp="a" * 64, status="reviewed")
    b = _unit("B")
    first = make_snapshot(
        revision_id="rev-id",
        route="INT_STEAM",
        game_version="3.2.0",
        revision="1042",
        locale="ko",
        units=[b, a],
    )
    second = make_snapshot(
        revision_id="rev-id",
        route="INT_STEAM",
        game_version="3.2.0",
        revision="1042",
        locale="ko",
        units=[a, b],
    )
    assert first.fingerprint == second.fingerprint
    assert [unit.key for unit in first.units] == ["A", "B"]

    changed = make_snapshot(
        revision_id="rev-id",
        route="INT_STEAM",
        game_version="3.2.0",
        revision="1042",
        locale="ko",
        units=[_unit("A", translation="둘", translation_fp="a" * 64), b],
    )
    assert changed.fingerprint != first.fingerprint
