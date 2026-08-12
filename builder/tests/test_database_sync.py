import pytest

from astral_builder.database.sync import (
    ExistingSourceState,
    SourceDisposition,
    plan_source_sync,
)
from astral_builder.formats.model import SourceStrings, TranslationKind, TranslationUnit


def _unit(key: str, text: str) -> TranslationUnit:
    return TranslationUnit(
        kind=TranslationKind.LANG,
        namespace="lang",
        key=key,
        source=SourceStrings(en=text),
    )


def test_source_sync_classifies_new_unchanged_changed_and_missing() -> None:
    old_a = _unit("A", "same")
    old_b = _unit("B", "old")
    old_c = _unit("C", "gone")
    incoming = [old_a, _unit("B", "new"), _unit("D", "added")]
    existing = {
        old_a.identity: ExistingSourceState("unit-a", old_a.source.fingerprint),
        old_b.identity: ExistingSourceState("unit-b", old_b.source.fingerprint),
        old_c.identity: ExistingSourceState("unit-c", old_c.source.fingerprint),
    }

    plan = plan_source_sync(incoming, existing)

    assert [item.disposition for item in plan.sources] == [
        SourceDisposition.UNCHANGED,
        SourceDisposition.CHANGED,
        SourceDisposition.NEW,
    ]
    assert plan.new_count == 1
    assert plan.changed_count == 1
    assert plan.unchanged_count == 1
    assert plan.missing_identities == (("lang", "lang", "C"),)


def test_source_sync_does_not_model_translation_deletion() -> None:
    old = _unit("A", "old")
    existing = {old.identity: ExistingSourceState("unit-a", old.source.fingerprint)}
    plan = plan_source_sync([], existing)
    assert plan.sources == ()
    assert plan.missing_identities == (old.identity,)


def test_source_sync_rejects_duplicate_incoming_identity() -> None:
    unit = _unit("A", "one")
    with pytest.raises(ValueError, match="duplicate"):
        plan_source_sync([unit, _unit("A", "two")], {})
