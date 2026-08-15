from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml

from astral_builder.addressables.catalog import AddressablesCatalog
from astral_builder.addressables.resolver import (
    BundleOrigin,
    ResolvedBundle,
    ResolvedTarget,
    resolve_target,
)
from astral_builder.database.repository import (
    AssetLocationInput,
    RevisionInput,
    SourceSyncResult,
    mark_revision_processed,
    sync_asset_locations,
    sync_revision_metadata,
    sync_revision_sources,
)
from astral_builder.extract.translations import extract_lang_units, extract_str_units
from astral_builder.extract.unity import extract_text_assets
from astral_builder.formats.model import TranslationUnit
from astral_builder.game.source import DownloadedCatalog, GameSource, GameSourceClient
from astral_builder.source.downloader import DownloadedBundle, RemoteBundleDownloader


@dataclass(frozen=True, slots=True)
class RouteSyncConfig:
    route: str
    platform: str
    translation_source_route: str
    lang_assets: dict[str, str]
    lang_target: str
    str_catalog_key: str
    str_asset_prefix: str
    str_target_field: str
    tmp_catalog_key: str
    tmp_asset_name: str
    legacy_font_name: str | None
    resources_root: str


@dataclass(frozen=True, slots=True)
class PreparedRevision:
    source: GameSource
    catalog_hash: str
    catalog: DownloadedCatalog
    units: tuple[TranslationUnit, ...]
    asset_locations: tuple[AssetLocationInput, ...]
    downloaded_bundles: tuple[DownloadedBundle, ...]
    empty_str_assets: tuple[str, ...]
    translation_source_route: str = "INT_STEAM"


@dataclass(frozen=True, slots=True)
class SyncRevisionResult:
    revision_id: str
    idempotent: bool
    unit_count: int
    source_added_count: int
    source_modified_count: int
    source_removed_count: int
    asset_location_count: int
    downloaded_bundle_count: int
    empty_str_assets: tuple[str, ...]


def load_route_sync_config(path: str | Path) -> RouteSyncConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("route config must be a mapping")
    try:
        route = str(data["route"]).upper()
        platform = str(data.get("platform", "windows")).lower()
        translation = data["translation"]
        lang_config = translation["lang"]
        lang_assets_raw = dict(lang_config["assets"])
        lang_assets = {str(key): str(value) for key, value in lang_assets_raw.items()}
        str_config = translation["str"]
        tmp_config = data["fonts"]["tmp"]
        legacy_config = data["fonts"].get("legacy")
        translation_source_route = str(translation.get("sourceRoute", route)).upper()
        default_lang_target = "en" if "en" in lang_assets else next(iter(lang_assets))
        lang_target = str(lang_config.get("target", default_lang_target))
        config = RouteSyncConfig(
            route=route,
            platform=platform,
            translation_source_route=translation_source_route,
            lang_assets=lang_assets,
            lang_target=lang_target,
            str_catalog_key=str(str_config["catalogKey"]),
            str_asset_prefix=str(str_config["assetPrefix"]),
            str_target_field=str(str_config.get("targetField", "en")),
            tmp_catalog_key=str(tmp_config["catalogKey"]),
            tmp_asset_name=str(tmp_config["asset"]),
            legacy_font_name=(str(legacy_config["asset"]) if legacy_config else None),
            resources_root=str(data.get("resources", {}).get("root", f"resources/{route.lower()}")),
        )
    except (KeyError, StopIteration, TypeError) as exc:
        raise ValueError("route config is missing required sync fields") from exc

    valid_codes = {"cn_s", "en", "jp", "cn_t"}
    if not config.lang_assets or not set(config.lang_assets).issubset(valid_codes):
        raise ValueError("route config language assets must use cn_s/en/jp/cn_t codes")
    if config.lang_target not in config.lang_assets:
        raise ValueError("route config lang target must reference a configured language asset")
    if config.str_target_field not in valid_codes:
        raise ValueError("route config STR targetField must be cn_s/en/jp/cn_t")
    if not config.translation_source_route:
        raise ValueError("translation source route cannot be empty")
    if config.platform not in {"windows", "android"}:
        raise ValueError(f"unsupported route platform: {config.platform}")
    return config


def _download_target(
    target: ResolvedTarget,
    *,
    downloader: RemoteBundleDownloader,
    root: Path,
    cache: dict[tuple[str, str], DownloadedBundle],
    progress: Callable[[str], None],
) -> tuple[DownloadedBundle, ...]:
    remote: list[ResolvedBundle] = []
    seen: set[tuple[str, str]] = set()
    for bundle in target.bundles:
        if bundle.origin is not BundleOrigin.REMOTE:
            continue
        identity = (bundle.bundle_name, bundle.cache_hash)
        if identity in seen:
            continue
        seen.add(identity)
        remote.append(bundle)

    total_bytes = sum(max(bundle.expected_size, 0) for bundle in remote)
    progress(
        f"{target.logical_name}: resolved {len(remote)} remote bundle(s), "
        f"expected {total_bytes / (1024 * 1024):.1f} MiB"
    )

    downloaded: list[DownloadedBundle] = []
    for index, bundle in enumerate(remote, start=1):
        identity = (bundle.bundle_name, bundle.cache_hash)
        cached = cache.get(identity)
        if cached is not None:
            progress(
                f"{target.logical_name}: reuse bundle {index}/{len(remote)} "
                f"({cached.size / (1024 * 1024):.1f} MiB)"
            )
            downloaded.append(cached)
            continue

        progress(
            f"{target.logical_name}: download bundle {index}/{len(remote)} "
            f"(expected {max(bundle.expected_size, 0) / (1024 * 1024):.1f} MiB)"
        )
        destination = root / bundle.cache_hash / f"{bundle.cache_hash}.bundle"
        item = downloader.download(bundle, destination)
        cache[identity] = item
        downloaded.append(item)
        progress(
            f"{target.logical_name}: downloaded bundle {index}/{len(remote)} "
            f"({item.size / (1024 * 1024):.1f} MiB)"
        )

    downloaded_tuple = tuple(downloaded)
    expected_remote = {
        (bundle.bundle_name, bundle.cache_hash)
        for bundle in target.bundles
        if bundle.origin is BundleOrigin.REMOTE
    }
    actual = {(item.resolved.bundle_name, item.resolved.cache_hash) for item in downloaded_tuple}
    if actual != expected_remote:
        raise RuntimeError(
            f"downloaded bundle set differs for {target.logical_name}: "
            f"expected={sorted(expected_remote)} actual={sorted(actual)}"
        )
    return downloaded_tuple


def _find_text_asset(
    bundles: tuple[DownloadedBundle, ...],
    asset_name: str,
    *,
    text_asset_cache: dict[Path, dict[str, bytes]],
) -> bytes:
    matches: list[bytes] = []
    for bundle in bundles:
        assets = text_asset_cache.get(bundle.path)
        if assets is None:
            assets = extract_text_assets(bundle.path)
            text_asset_cache[bundle.path] = assets
        if asset_name in assets:
            matches.append(assets[asset_name])
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one TextAsset named {asset_name!r}, found {len(matches)}"
        )
    return matches[0]


def _bundle_locations(
    target: ResolvedTarget,
    downloaded: tuple[DownloadedBundle, ...],
) -> tuple[AssetLocationInput, ...]:
    by_identity = {
        (item.resolved.bundle_name, item.resolved.cache_hash): item for item in downloaded
    }
    remote_bundles = []
    seen: set[tuple[str, str]] = set()
    for bundle in target.bundles:
        if bundle.origin is not BundleOrigin.REMOTE:
            continue
        identity = (bundle.bundle_name, bundle.cache_hash)
        if identity in seen:
            continue
        seen.add(identity)
        remote_bundles.append(bundle)

    rows: list[AssetLocationInput] = []
    multiple_bundles = len(remote_bundles) > 1
    for bundle in remote_bundles:
        item = by_identity[(bundle.bundle_name, bundle.cache_hash)]
        asset_name = (
            f"{bundle.bundle_name}/{bundle.cache_hash}" if multiple_bundles else target.catalog_key
        )
        rows.append(
            AssetLocationInput(
                logical_name=target.logical_name,
                catalog_key=target.catalog_key,
                origin="remote",
                asset_type="AssetBundle",
                asset_name=asset_name,
                bundle_name=bundle.bundle_name,
                bundle_hash=bundle.cache_hash,
                cache_root=bundle.bundle_name,
                source_sha256=item.sha256,
                source_size=item.size,
            )
        )
    return tuple(rows)


def prepare_revision(
    *,
    config: RouteSyncConfig,
    game_version: str,
    work_dir: str | Path,
    client: GameSourceClient | None = None,
    downloader: RemoteBundleDownloader | None = None,
    progress: Callable[[str], None] | None = None,
) -> PreparedRevision:
    source_client = client or GameSourceClient()
    bundle_downloader = downloader or RemoteBundleDownloader()
    if progress is None:
        def default_progress(message: str) -> None:
            print(f"[sync:{config.route}] {message}", flush=True)

        progress = default_progress
    work_root = Path(work_dir)
    catalog_path = work_root / "catalog" / f"catalog_{game_version}.json"
    bundles_root = work_root / "bundles"

    progress("discover remote revision")
    source = source_client.discover(config.route, game_version)
    progress(f"revision r{source.revision}: fetch catalog hash")
    catalog_hash = source_client.fetch_catalog_hash(source)
    progress(f"revision r{source.revision}: download catalog")
    catalog_download = source_client.download_catalog(source, catalog_path)
    progress(
        f"catalog downloaded ({catalog_download.size / (1024 * 1024):.1f} MiB); parse Addressables"
    )
    catalog = AddressablesCatalog.from_path(catalog_download.path)

    all_downloads: dict[tuple[str, str], DownloadedBundle] = {}
    asset_locations: list[AssetLocationInput] = []
    language_payloads: dict[str, bytes] = {}
    text_asset_cache: dict[Path, dict[str, bytes]] = {}

    for code, asset_name in config.lang_assets.items():
        target = resolve_target(
            catalog,
            source,
            logical_name=f"lang:{code}",
            catalog_key=asset_name,
        )
        downloaded = _download_target(
            target,
            downloader=bundle_downloader,
            root=bundles_root,
            cache=all_downloads,
            progress=progress,
        )
        for item in downloaded:
            all_downloads[(item.resolved.bundle_name, item.resolved.cache_hash)] = item
        asset_locations.extend(_bundle_locations(target, downloaded))
        if config.translation_source_route == config.route:
            progress(f"lang:{code}: extract TextAsset {asset_name!r}")
            language_payloads[code] = _find_text_asset(
                downloaded, asset_name, text_asset_cache=text_asset_cache
            )
            progress(f"lang:{code}: extraction complete")

    str_target = resolve_target(
        catalog,
        source,
        logical_name="str",
        catalog_key=config.str_catalog_key,
    )
    str_downloaded = _download_target(
        str_target,
        downloader=bundle_downloader,
        root=bundles_root,
        cache=all_downloads,
        progress=progress,
    )
    for item in str_downloaded:
        all_downloads[(item.resolved.bundle_name, item.resolved.cache_hash)] = item
    asset_locations.extend(_bundle_locations(str_target, str_downloaded))

    str_assets: dict[str, bytes] = {}
    if config.translation_source_route == config.route:
        progress("str: extract translation TextAssets")
        for item in str_downloaded:
            assets = text_asset_cache.get(item.path)
            if assets is None:
                assets = extract_text_assets(item.path)
                text_asset_cache[item.path] = assets
            str_assets.update(
                {
                    name: payload
                    for name, payload in assets.items()
                    if name.startswith(config.str_asset_prefix)
                }
            )
        progress(f"str: extracted {len(str_assets)} TextAsset(s)")
    empty_str_assets = tuple(sorted(name for name, payload in str_assets.items() if not payload))

    tmp_target = resolve_target(
        catalog,
        source,
        logical_name="tmp-font",
        catalog_key=config.tmp_catalog_key,
    )
    tmp_downloaded = _download_target(
        tmp_target,
        downloader=bundle_downloader,
        root=bundles_root,
        cache=all_downloads,
        progress=progress,
    )
    for item in tmp_downloaded:
        all_downloads[(item.resolved.bundle_name, item.resolved.cache_hash)] = item
    asset_locations.extend(_bundle_locations(tmp_target, tmp_downloaded))

    if config.translation_source_route == config.route:
        progress("convert extracted source text to translation units")
        lang_units = extract_lang_units(language_payloads)
        str_units = extract_str_units(str_assets, asset_prefix=config.str_asset_prefix)
        units = lang_units + str_units
        progress(f"prepared {len(units)} canonical translation unit(s)")
    else:
        units = ()

    progress(f"prepare complete: {len(all_downloads)} unique bundle(s)")
    return PreparedRevision(
        source=source,
        catalog_hash=catalog_hash,
        catalog=catalog_download,
        units=units,
        asset_locations=tuple(asset_locations),
        downloaded_bundles=tuple(all_downloads.values()),
        empty_str_assets=empty_str_assets,
        translation_source_route=config.translation_source_route,
    )


def persist_prepared_revision(
    conn: psycopg.Connection,
    prepared: PreparedRevision,
) -> SyncRevisionResult:
    revision = RevisionInput(
        route=prepared.source.route,
        game_version=prepared.source.version,
        revision=prepared.source.revision,
        source_url=prepared.source.source_url,
        catalog_url=prepared.source.catalog_url,
        catalog_sha256=prepared.catalog.sha256,
        catalog_build_hash=prepared.catalog_hash,
    )
    if prepared.translation_source_route == prepared.source.route:
        source_result: SourceSyncResult = sync_revision_sources(conn, revision, prepared.units)
        revision_id = source_result.revision_id
        source_idempotent = source_result.idempotent
        source_added_count = source_result.plan.new_count
        source_modified_count = source_result.plan.changed_count
        source_removed_count = source_result.plan.removed_count
    else:
        revision_id, source_idempotent = sync_revision_metadata(conn, revision)
        source_added_count = source_modified_count = source_removed_count = 0

    locations_idempotent = sync_asset_locations(
        conn,
        revision_id,
        prepared.asset_locations,
    )
    mark_revision_processed(conn, revision_id)
    return SyncRevisionResult(
        revision_id=str(revision_id),
        idempotent=source_idempotent and locations_idempotent,
        unit_count=len(prepared.units),
        source_added_count=source_added_count,
        source_modified_count=source_modified_count,
        source_removed_count=source_removed_count,
        asset_location_count=len(prepared.asset_locations),
        downloaded_bundle_count=len(prepared.downloaded_bundles),
        empty_str_assets=prepared.empty_str_assets,
    )


def write_sync_github_output(
    result: SyncRevisionResult,
    prepared: PreparedRevision,
    destination: str | Path,
) -> None:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        f"revision_id={result.revision_id}",
        f"route={prepared.source.route}",
        f"game_version={prepared.source.version}",
        f"revision={prepared.source.revision}",
        f"catalog_hash={prepared.catalog_hash}",
        f"source_added={result.source_added_count}",
        f"source_modified={result.source_modified_count}",
        f"source_removed={result.source_removed_count}",
    )
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines) + "\n")
