import pytest

from astral_builder.database.repository import (
    RevisionConflictError,
    assert_idempotent_source_match,
)
from astral_builder.formats.model import SourceStrings, TranslationKind, TranslationUnit


def _unit(key: str, text: str) -> TranslationUnit:
    return TranslationUnit(
        kind=TranslationKind.STR,
        namespace="STRCard",
        key=key,
        source=SourceStrings(en=text),
    )


def test_idempotent_source_match_accepts_same_snapshot() -> None:
    units = (_unit("1", "one"), _unit("2", "two"))
    persisted = {unit.identity: unit.source.fingerprint for unit in units}
    assert_idempotent_source_match(units, persisted)


def test_idempotent_source_match_rejects_changed_snapshot() -> None:
    old = _unit("1", "old")
    with pytest.raises(RevisionConflictError, match="changed"):
        assert_idempotent_source_match(
            (_unit("1", "new"),),
            {old.identity: old.source.fingerprint},
        )


def test_idempotent_source_match_rejects_added_or_removed_units() -> None:
    old = _unit("1", "one")
    with pytest.raises(RevisionConflictError, match="added"):
        assert_idempotent_source_match(
            (old, _unit("2", "two")),
            {old.identity: old.source.fingerprint},
        )
