from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from astral_builder.database.snapshot import SnapshotUnit, TranslationSnapshot, TranslationState
from astral_builder.formats.astral_str import StrDocument, StrEntry, decode_str, encode_str
from astral_builder.formats.lang_xml import decode_lang_xml, encode_lang_xml
from astral_builder.formats.model import SourceStrings


class BuildChannel(StrEnum):
    PREVIEW = "preview"
    STABLE = "stable"


@dataclass(frozen=True, slots=True)
class PatchStats:
    total_units: int
    translated_units: int
    skipped_units: int


def select_translation(unit: SnapshotUnit, channel: BuildChannel) -> str | None:
    if not unit.translation:
        return None
    if channel is BuildChannel.PREVIEW:
        return unit.translation
    if unit.state is TranslationState.APPROVED:
        return unit.translation
    return None


def _snapshot_map(
    snapshot: TranslationSnapshot,
    *,
    kind: str,
    namespace: str,
) -> dict[str, SnapshotUnit]:
    return {
        unit.key: unit
        for unit in snapshot.units
        if unit.kind == kind and unit.namespace == namespace
    }


def patch_lang_payload(
    source_payload: bytes | str,
    snapshot: TranslationSnapshot,
    *,
    namespace: str = "lang",
    channel: BuildChannel = BuildChannel.PREVIEW,
) -> tuple[bytes, PatchStats]:
    source = decode_lang_xml(source_payload)
    units = _snapshot_map(snapshot, kind="lang", namespace=namespace)
    translated = 0

    patched: dict[str, str] = {}
    for key, value in source.items():
        unit = units.get(key)
        replacement = None if unit is None else select_translation(unit, channel)
        if replacement is None:
            patched[key] = value
        else:
            patched[key] = replacement
            translated += 1

    return (
        encode_lang_xml(patched),
        PatchStats(
            total_units=len(source),
            translated_units=translated,
            skipped_units=len(source) - translated,
        ),
    )


def _replace_source_field(source: SourceStrings, field: str, value: str) -> SourceStrings:
    if field not in {"cn_s", "en", "jp", "cn_t"}:
        raise ValueError(f"unsupported STR target field: {field}")
    values = {
        "cn_s": source.cn_s,
        "en": source.en,
        "jp": source.jp,
        "cn_t": source.cn_t,
    }
    values[field] = value
    return SourceStrings(**values)


def patch_str_payload(
    source_payload: bytes,
    snapshot: TranslationSnapshot,
    *,
    namespace: str,
    target_field: str,
    channel: BuildChannel = BuildChannel.PREVIEW,
) -> tuple[bytes, PatchStats]:
    if not source_payload:
        return b"", PatchStats(total_units=0, translated_units=0, skipped_units=0)

    document = decode_str(source_payload)
    units = _snapshot_map(snapshot, kind="str", namespace=namespace)
    patched_entries: list[StrEntry] = []
    translated = 0

    for entry in document.entries:
        unit = units.get(str(entry.id))
        replacement = None if unit is None else select_translation(unit, channel)
        if replacement is None:
            patched_entries.append(entry)
            continue
        translated += 1
        patched_entries.append(
            StrEntry(
                id=entry.id,
                source=_replace_source_field(entry.source, target_field, replacement),
            )
        )

    patched = StrDocument(
        entries=tuple(patched_entries),
        paired=document.paired,
        mirrors_grouped=document.mirrors_grouped,
    )
    return (
        encode_str(patched),
        PatchStats(
            total_units=len(document.entries),
            translated_units=translated,
            skipped_units=len(document.entries) - translated,
        ),
    )
