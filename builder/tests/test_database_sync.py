from uuid import uuid4

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


def _state(unit: TranslationUnit) -> ExistingSourceState:
    return ExistingSourceState(uuid4(), uuid4(), unit.source.fingerprint)


def test_source_sync_classifies_new_unchanged_changed_and_removed() -> None:
    old_a = _unit("A", "same")
    old_b = _unit("B", "old")
    old_c = _unit("C", "gone")
    incoming = [old_a, _unit("B", "new"), _unit("D", "added")]
    states = {unit.identity: _state(unit) for unit in (old_a, old_b, old_c)}

    plan = plan_source_sync(incoming, states)

    assert [item.disposition for item in plan.sources] == [
        SourceDisposition.UNCHANGED,
        SourceDisposition.CHANGED,
        SourceDisposition.NEW,
    ]
    assert plan.new_count == 1
    assert plan.changed_count == 1
    assert plan.unchanged_count == 1
    assert plan.removed == (states[old_c.identity],)
    assert plan.removed_count == 1


def test_removed_source_is_history_not_translation_deletion() -> None:
    old = _unit("A", "old")
    state = _state(old)
    plan = plan_source_sync([], {old.identity: state})
    assert plan.sources == ()
    assert plan.removed == (state,)


def test_source_sync_rejects_duplicate_incoming_identity() -> None:
    unit = _unit("A", "one")
    with pytest.raises(ValueError, match="duplicate"):
        plan_source_sync([unit, _unit("A", "two")], {})
