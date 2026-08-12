from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from astral_builder.formats.model import TranslationUnit

Identity = tuple[str, str, str]


class SourceDisposition(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class ExistingSourceState:
    unit_id: str
    source_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class PlannedSource:
    unit: TranslationUnit
    unit_id: str | None
    disposition: SourceDisposition


@dataclass(frozen=True, slots=True)
class SourceSyncPlan:
    sources: tuple[PlannedSource, ...]
    missing_identities: tuple[Identity, ...]

    @property
    def new_count(self) -> int:
        return sum(source.disposition is SourceDisposition.NEW for source in self.sources)

    @property
    def changed_count(self) -> int:
        return sum(source.disposition is SourceDisposition.CHANGED for source in self.sources)

    @property
    def unchanged_count(self) -> int:
        return sum(source.disposition is SourceDisposition.UNCHANGED for source in self.sources)


def plan_source_sync(
    units: Iterable[TranslationUnit],
    existing: Mapping[Identity, ExistingSourceState],
) -> SourceSyncPlan:
    """Plan source synchronization without mutating translations.

    ``existing`` represents each unit's most recent source fingerprint before the incoming
    revision. Missing units are reported but are not deleted; historical rows and translations
    remain intact.
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
        planned.append(PlannedSource(unit, state.unit_id, disposition))

    incoming = set(identities)
    missing = tuple(sorted(identity for identity in existing if identity not in incoming))
    return SourceSyncPlan(tuple(planned), missing)
