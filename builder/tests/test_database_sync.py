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


class _CopySink:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def write_row(self, row: tuple[object, ...]) -> None:
        self.rows.append(row)


class _BulkCursor:
    def __init__(self, *, source_rows: int = 0, new_units: int = 0, removed_rows: int = 0) -> None:
        self.source_rows = source_rows
        self.new_units = new_units
        self.removed_rows = removed_rows
        self.rowcount = -1
        self.executed: list[str] = []
        self.copied: list[tuple[object, ...]] = []

    def execute(self, query: str, _params=None) -> None:
        normalized = " ".join(query.split())
        self.executed.append(normalized)
        if normalized.startswith("INSERT INTO translation_units"):
            self.rowcount = self.new_units
        elif normalized.startswith("INSERT INTO source_changes"):
            self.rowcount = (
                self.removed_rows if "FROM source_remove_stage" in normalized else self.source_rows
            )
        elif normalized.startswith("UPDATE translation_units"):
            self.rowcount = (
                self.removed_rows if "source_remove_stage" in normalized else self.source_rows
            )
        else:
            self.rowcount = -1

    def copy(self, _query: str) -> _CopySink:
        return _CopySink(self.copied)


def test_source_stage_bulk_write_count_is_constant_for_large_initial_import() -> None:
    from astral_builder.database.repository import (
        _bulk_persist_source_stage,
        _prepare_source_stage_rows,
    )

    units = tuple(_unit(f"K{index}", f"text-{index}") for index in range(1000))
    plan = plan_source_sync(units, {})
    rows = _prepare_source_stage_rows(plan, {})
    cursor = _BulkCursor(source_rows=len(rows), new_units=len(rows))
    progress: list[str] = []

    _bulk_persist_source_stage(
        cursor,  # type: ignore[arg-type]
        revision_id=uuid4(),
        rows=rows,
        progress=progress.append,
    )

    assert len(rows) == 1000
    assert len(cursor.copied) == 1000
    assert len(cursor.executed) == 7
    assert any("COPY" not in statement for statement in cursor.executed)
    assert any("ON CONFLICT (unit_id, source_fingerprint) DO NOTHING" in q for q in cursor.executed)
    assert any("JOIN source_versions AS versions" in q for q in cursor.executed)
    assert any("created_by" in q and "'bot'" in q for q in cursor.executed if "INSERT INTO source_changes" in q)
    assert any("UPDATE translation_changes" in q and "status = 'superseded'" in q for q in cursor.executed)
    assert progress[0] == "database: stage 1000 added/modified source row(s)"


def test_source_stage_reuses_existing_unit_identity_when_readded() -> None:
    from astral_builder.database.repository import _prepare_source_stage_rows

    unit = _unit("A", "returned")
    existing_unit_id = uuid4()
    plan = plan_source_sync((unit,), {})

    rows = _prepare_source_stage_rows(plan, {unit.identity: existing_unit_id})

    assert len(rows) == 1
    assert rows[0].unit_id == existing_unit_id
    assert rows[0].is_new_unit is False
    assert rows[0].change_type == "added"


def test_removed_sources_use_one_copy_and_fixed_number_of_statements() -> None:
    from astral_builder.database.repository import _bulk_persist_removed_sources

    removed = tuple(ExistingSourceState(uuid4(), uuid4(), "a" * 64) for _ in range(500))
    cursor = _BulkCursor(removed_rows=len(removed))

    _bulk_persist_removed_sources(
        cursor,  # type: ignore[arg-type]
        revision_id=uuid4(),
        removed=removed,
        progress=lambda _message: None,
    )

    assert len(cursor.copied) == 500
    assert len(cursor.executed) == 5
    assert any("INSERT INTO source_changes" in q and "'bot'" in q for q in cursor.executed)
    assert any("UPDATE translation_changes" in q and "status = 'superseded'" in q for q in cursor.executed)
    assert any("UPDATE translation_units" in q for q in cursor.executed)
