from astral_builder.automation.translation_status import summarize_translation_snapshot
from astral_builder.database.snapshot import SnapshotUnit, make_snapshot
from astral_builder.formats.model import SourceStrings


def _unit(key: str, *, text: str = "", status: str | None = None, fp: str | None = None):
    return SnapshotUnit(
        kind="lang",
        namespace="lang",
        key=key,
        source=SourceStrings(en=f"source-{key}"),
        source_fingerprint="a" * 64,
        translation=text,
        translation_status=status,
        translation_source_fingerprint=fp,
    )


def test_untranslated_units_are_reported_but_release_remains_safe() -> None:
    snapshot = make_snapshot(
        revision_id="rev",
        route="INT_STEAM",
        game_version="3.2.0",
        revision="116",
        locale="ko",
        units=[
            _unit("A", text="번역", status="approved", fp="a" * 64),
            _unit("B"),
        ],
    )
    report = summarize_translation_snapshot(snapshot)
    assert report.total == 2
    assert report.approved == 1
    assert report.untranslated == 1
    assert report.incomplete == 1
    assert report.releasable is True
    assert report.examples == ("untranslated: lang/lang/B",)


def test_stale_translation_is_visible_as_needs_review() -> None:
    snapshot = make_snapshot(
        revision_id="rev",
        route="INT_STEAM",
        game_version="3.2.0",
        revision="116",
        locale="ko",
        units=[_unit("A", text="이전 번역", status="approved", fp="b" * 64)],
    )
    report = summarize_translation_snapshot(snapshot)
    assert report.needs_review == 1
    assert report.approved == 0
    assert report.releasable is True
