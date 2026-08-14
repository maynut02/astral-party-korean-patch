from astral_builder.database.snapshot import SnapshotUnit, make_snapshot
from astral_builder.formats.model import SourceStrings


def _unit(key: str, *, translation: str = "") -> SnapshotUnit:
    return SnapshotUnit(
        kind="lang",
        namespace="lang",
        key=key,
        source=SourceStrings(en=f"source-{key}"),
        source_version_id=f"source-version-{key}",
        translation=translation,
    )


def test_snapshot_translation_presence_is_the_only_build_state() -> None:
    assert _unit("A").translated is False
    assert _unit("A", translation="번역").translated is True


def test_snapshot_fingerprint_is_order_independent_and_content_sensitive() -> None:
    a = _unit("A", translation="하나")
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
        units=[_unit("A", translation="둘"), b],
    )
    assert changed.fingerprint != first.fingerprint
