from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from astral_builder.database.snapshot import TranslationSnapshot, TranslationState


@dataclass(frozen=True, slots=True)
class TranslationStatusReport:
    total: int
    approved: int
    untranslated: int
    draft: int
    reviewed: int
    needs_review: int
    examples: tuple[str, ...]

    @property
    def incomplete(self) -> int:
        return self.total - self.approved

    @property
    def releasable(self) -> bool:
        return self.total > 0


def summarize_translation_snapshot(
    snapshot: TranslationSnapshot,
    *,
    example_limit: int = 20,
) -> TranslationStatusReport:
    counts = Counter(unit.state for unit in snapshot.units)
    examples = tuple(
        f"{unit.state.value}: {unit.kind}/{unit.namespace}/{unit.key}"
        for unit in snapshot.units
        if unit.state is not TranslationState.APPROVED
    )[:example_limit]
    return TranslationStatusReport(
        total=len(snapshot.units),
        approved=counts[TranslationState.APPROVED],
        untranslated=counts[TranslationState.UNTRANSLATED],
        draft=counts[TranslationState.DRAFT],
        reviewed=counts[TranslationState.REVIEWED],
        needs_review=counts[TranslationState.NEEDS_REVIEW],
        examples=examples,
    )


def write_translation_status_github_output(
    report: TranslationStatusReport,
    destination: str | Path,
) -> None:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(f"total={report.total}\n")
        file.write(f"approved={report.approved}\n")
        file.write(f"untranslated={report.untranslated}\n")
        file.write(f"draft={report.draft}\n")
        file.write(f"reviewed={report.reviewed}\n")
        file.write(f"needs_review={report.needs_review}\n")
        file.write(f"incomplete={report.incomplete}\n")
