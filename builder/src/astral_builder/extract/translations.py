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
    unknown = sorted(set(language_payloads) - set(_LANGUAGE_CODES))
    if unknown:
        raise ValueError(f"unsupported language payload codes: {unknown}")
    if not language_payloads:
        raise ValueError("at least one language payload is required")

    decoded = {
        code: decode_lang_xml(language_payloads[code])
        for code in _LANGUAGE_CODES
        if code in language_payloads
    }
    all_keys = set().union(*(mapping.keys() for mapping in decoded.values()))

    # Prefer English for human-facing stability, then the first available canonical language.
    order_code = (
        "en" if "en" in decoded else next(code for code in _LANGUAGE_CODES if code in decoded)
    )
    ordered_keys = list(decoded[order_code])
    ordered_keys.extend(sorted(all_keys - set(ordered_keys)))

    return tuple(
        TranslationUnit(
            kind=TranslationKind.LANG,
            namespace=namespace,
            key=key,
            source=SourceStrings(
                cn_s=decoded.get("cn_s", {}).get(key, ""),
                en=decoded.get("en", {}).get(key, ""),
                jp=decoded.get("jp", {}).get(key, ""),
                cn_t=decoded.get("cn_t", {}).get(key, ""),
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
