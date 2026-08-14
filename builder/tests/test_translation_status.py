from astral_builder.automation.translation_status import summarize_translation_snapshot
from astral_builder.database.snapshot import SnapshotUnit, make_snapshot
from astral_builder.formats.model import SourceStrings


def _unit(key: str, *, text: str = "") -> SnapshotUnit:
    return SnapshotUnit(
        kind="lang",
        namespace="lang",
        key=key,
        source=SourceStrings(en=f"source-{key}"),
        source_version_id=f"source-{key}",
        translation=text,
    )


def test_untranslated_and_pending_are_reported_without_affecting_production_value() -> None:
    snapshot = make_snapshot(
        revision_id="rev",
        route="INT_STEAM",
        game_version="3.2.0",
        revision="116",
        locale="ko",
        units=[_unit("A", text="승인 번역"), _unit("B")],
    )
    report = summarize_translation_snapshot(snapshot, pending=3)
    assert report.total == 2
    assert report.approved == 1
    assert report.untranslated == 1
    assert report.pending == 3
    assert report.incomplete == 1
    assert report.releasable is True
    assert report.examples == ("untranslated: lang/lang/B",)
