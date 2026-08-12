from __future__ import annotations

from collections.abc import Mapping

from astral_builder.formats.astral_str import decode_str
from astral_builder.formats.lang_xml import decode_lang_xml
from astral_builder.formats.model import SourceStrings, TranslationKind, TranslationUnit

_LANGUAGE_CODES = ("cn_s", "en", "jp", "cn_t")


def extract_lang_units(
    language_payloads: Mapping[str, bytes | str],
    *,
    namespace: str = "lang",
) -> tuple[TranslationUnit, ...]:
    missing = [code for code in _LANGUAGE_CODES if code not in language_payloads]
    if missing:
        raise ValueError(f"missing language payloads: {missing}")

    decoded = {code: decode_lang_xml(language_payloads[code]) for code in _LANGUAGE_CODES}
    all_keys = set().union(*(mapping.keys() for mapping in decoded.values()))

    # English order is the most useful stable human-facing order; keys absent there follow sorted.
    ordered_keys = list(decoded["en"])
    ordered_keys.extend(sorted(all_keys - set(ordered_keys)))

    return tuple(
        TranslationUnit(
            kind=TranslationKind.LANG,
            namespace=namespace,
            key=key,
            source=SourceStrings(
                cn_s=decoded["cn_s"].get(key, ""),
                en=decoded["en"].get(key, ""),
                jp=decoded["jp"].get(key, ""),
                cn_t=decoded["cn_t"].get(key, ""),
            ),
        )
        for key in ordered_keys
    )


def extract_str_units(
    text_assets: Mapping[str, bytes],
    *,
    asset_prefix: str = "STR",
) -> tuple[TranslationUnit, ...]:
    units: list[TranslationUnit] = []
    for asset_name in sorted(text_assets):
        if not asset_name.startswith(asset_prefix):
            continue
        payload = text_assets[asset_name]
        if not payload:
            continue
        document = decode_str(payload)
        units.extend(
            TranslationUnit(
                kind=TranslationKind.STR,
                namespace=asset_name,
                key=str(entry.id),
                source=entry.source,
            )
            for entry in document.entries
        )
    return tuple(units)
