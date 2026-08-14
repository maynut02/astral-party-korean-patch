from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from astral_builder.database.snapshot import TranslationSnapshot


@dataclass(frozen=True, slots=True)
class TranslationStatusReport:
    total: int
    approved: int
    untranslated: int
    pending: int
    examples: tuple[str, ...]

    @property
    def incomplete(self) -> int:
        return self.untranslated

    @property
    def releasable(self) -> bool:
        return self.total > 0


def summarize_translation_snapshot(
    snapshot: TranslationSnapshot,
    *,
    pending: int = 0,
    example_limit: int = 20,
) -> TranslationStatusReport:
    untranslated_units = tuple(unit for unit in snapshot.units if not unit.translated)
    return TranslationStatusReport(
        total=len(snapshot.units),
        approved=len(snapshot.units) - len(untranslated_units),
        untranslated=len(untranslated_units),
        pending=pending,
        examples=tuple(
            f"untranslated: {unit.kind}/{unit.namespace}/{unit.key}"
            for unit in untranslated_units[:example_limit]
        ),
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
        file.write(f"pending={report.pending}\n")
        file.write(f"incomplete={report.incomplete}\n")
