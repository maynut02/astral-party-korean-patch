from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from astral_builder.database.snapshot import TranslationSnapshot
from astral_builder.extract.unity import extract_text_assets
from astral_builder.patch.translations import (
    DistributionChannel,
    PatchStats,
    patch_lang_payload,
    patch_str_payload,
)
from astral_builder.patch.unity import patch_text_assets


@dataclass(frozen=True, slots=True)
class BundlePatchResult:
    output_path: Path
    assets: tuple[str, ...]
    stats: PatchStats


def patch_lang_bundle(
    input_path: str | Path,
    output_path: str | Path,
    snapshot: TranslationSnapshot,
    *,
    asset_name: str = "English",
    namespace: str = "lang",
    channel: DistributionChannel = DistributionChannel.RELEASE,
) -> BundlePatchResult:
    source = extract_text_assets(input_path, names={asset_name})[asset_name]
    payload, stats = patch_lang_payload(
        source,
        snapshot,
        namespace=namespace,
        channel=channel,
    )
    assets = patch_text_assets(input_path, output_path, {asset_name: payload})
    return BundlePatchResult(Path(output_path), assets, stats)


def patch_str_bundle(
    input_path: str | Path,
    output_path: str | Path,
    snapshot: TranslationSnapshot,
    *,
    asset_prefix: str = "STR",
    target_field: str = "en",
    channel: DistributionChannel = DistributionChannel.RELEASE,
) -> BundlePatchResult:
    source_assets = extract_text_assets(input_path, name_prefix=asset_prefix)
    replacements: dict[str, bytes] = {}
    total = translated = skipped = 0
    for name, source in source_assets.items():
        payload, stats = patch_str_payload(
            source,
            snapshot,
            namespace=name,
            target_field=target_field,
            channel=channel,
        )
        replacements[name] = payload
        total += stats.total_units
        translated += stats.translated_units
        skipped += stats.skipped_units

    assets = patch_text_assets(input_path, output_path, replacements)
    return BundlePatchResult(
        output_path=Path(output_path),
        assets=assets,
        stats=PatchStats(total, translated, skipped),
    )
