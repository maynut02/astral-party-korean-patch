from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from astral_builder.formats.model import TranslationUnit

Identity = tuple[str, str, str]


class SourceDisposition(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class ExistingSourceState:
    unit_id: UUID
    source_version_id: UUID
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class PlannedSource:
    unit: TranslationUnit
    state: ExistingSourceState | None
    disposition: SourceDisposition


@dataclass(frozen=True, slots=True)
class SourceSyncPlan:
    sources: tuple[PlannedSource, ...]
    removed: tuple[ExistingSourceState, ...]

    @property
    def new_count(self) -> int:
        return sum(item.disposition is SourceDisposition.NEW for item in self.sources)

    @property
    def changed_count(self) -> int:
        return sum(item.disposition is SourceDisposition.CHANGED for item in self.sources)

    @property
    def unchanged_count(self) -> int:
        return sum(item.disposition is SourceDisposition.UNCHANGED for item in self.sources)

    @property
    def removed_count(self) -> int:
        return len(self.removed)


def plan_source_sync(
    units: Iterable[TranslationUnit],
    existing: Mapping[Identity, ExistingSourceState],
) -> SourceSyncPlan:
    """Compare an INT_STEAM source scan with the currently applied source state.

    Only new/changed/removed units need persistence. Unchanged source versions are referenced by
    ``translation_units.current_source_version_id`` and are never duplicated per game revision.
    """
    unit_list = tuple(units)
    identities = [unit.identity for unit in unit_list]
    if len(set(identities)) != len(identities):
        raise ValueError("incoming translation units contain duplicate identities")

    planned: list[PlannedSource] = []
    for unit in unit_list:
        state = existing.get(unit.identity)
        if state is None:
            planned.append(PlannedSource(unit, None, SourceDisposition.NEW))
            continue
        disposition = (
            SourceDisposition.UNCHANGED
            if state.source_fingerprint == unit.source.fingerprint
            else SourceDisposition.CHANGED
        )
        planned.append(PlannedSource(unit, state, disposition))

    incoming = set(identities)
    removed = tuple(existing[identity] for identity in sorted(existing) if identity not in incoming)
    return SourceSyncPlan(tuple(planned), removed)
