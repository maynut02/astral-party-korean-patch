from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml

from astral_builder.addressables.catalog import AddressablesCatalog
from astral_builder.addressables.resolver import BundleOrigin, ResolvedTarget, resolve_target
from astral_builder.database.repository import (
    AssetLocationInput,
    RevisionInput,
    SourceSyncResult,
    mark_revision_processed,
    sync_asset_locations,
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
    lang_assets: dict[str, str]
    str_catalog_key: str
    str_asset_prefix: str
    tmp_catalog_key: str
    tmp_asset_name: str


@dataclass(frozen=True, slots=True)
class PreparedRevision:
    source: GameSource
    catalog_hash: str
    catalog: DownloadedCatalog
    units: tuple[TranslationUnit, ...]
    asset_locations: tuple[AssetLocationInput, ...]
    downloaded_bundles: tuple[DownloadedBundle, ...]
    empty_str_assets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyncRevisionResult:
    revision_id: str
    idempotent: bool
    unit_count: int
    asset_location_count: int
    downloaded_bundle_count: int
    empty_str_assets: tuple[str, ...]


def load_route_sync_config(path: str | Path) -> RouteSyncConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("route config must be a mapping")
    try:
        lang_assets = dict(data["translation"]["lang"]["assets"])
        str_config = data["translation"]["str"]
        tmp_config = data["fonts"]["tmp"]
        config = RouteSyncConfig(
            route=str(data["route"]),
            lang_assets={str(key): str(value) for key, value in lang_assets.items()},
            str_catalog_key=str(str_config["catalogKey"]),
            str_asset_prefix=str(str_config["assetPrefix"]),
            tmp_catalog_key=str(tmp_config["catalogKey"]),
            tmp_asset_name=str(tmp_config["asset"]),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("route config is missing required sync fields") from exc
    if set(config.lang_assets) != {"cn_s", "en", "jp", "cn_t"}:
        raise ValueError("route config must define cn_s/en/jp/cn_t language assets")
    return config


def _download_target(
    target: ResolvedTarget,
    *,
    downloader: RemoteBundleDownloader,
    root: Path,
) -> tuple[DownloadedBundle, ...]:
    downloaded = downloader.download_target(target.bundles, root)
    expected_remote = {
        (bundle.bundle_name, bundle.cache_hash)
        for bundle in target.bundles
        if bundle.origin is BundleOrigin.REMOTE
    }
    actual = {(item.resolved.bundle_name, item.resolved.cache_hash) for item in downloaded}
    if actual != expected_remote:
        raise RuntimeError(
            f"downloaded bundle set differs for {target.logical_name}: "
            f"expected={sorted(expected_remote)} actual={sorted(actual)}"
        )
    return downloaded


def _find_text_asset(
    bundles: tuple[DownloadedBundle, ...],
    asset_name: str,
) -> bytes:
    matches: list[bytes] = []
    for bundle in bundles:
        assets = extract_text_assets(bundle.path)
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
        (item.resolved.bundle_name, item.resolved.cache_hash): item
        for item in downloaded
    }
    rows: list[AssetLocationInput] = []
    for bundle in target.bundles:
        if bundle.origin is not BundleOrigin.REMOTE:
            continue
        item = by_identity[(bundle.bundle_name, bundle.cache_hash)]
        rows.append(
            AssetLocationInput(
                logical_name=target.logical_name,
                catalog_key=target.catalog_key,
                origin="remote",
                asset_type="AssetBundle",
                asset_name=target.catalog_key,
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
) -> PreparedRevision:
    source_client = client or GameSourceClient()
    bundle_downloader = downloader or RemoteBundleDownloader()
    work_root = Path(work_dir)
    catalog_path = work_root / "catalog" / f"catalog_{game_version}.json"
    bundles_root = work_root / "bundles"

    source = source_client.discover(config.route, game_version)
    catalog_hash = source_client.fetch_catalog_hash(source)
    catalog_download = source_client.download_catalog(source, catalog_path)
    catalog = AddressablesCatalog.from_path(catalog_download.path)

    all_downloads: dict[tuple[str, str], DownloadedBundle] = {}
    asset_locations: list[AssetLocationInput] = []
    language_payloads: dict[str, bytes] = {}

    for code, asset_name in config.lang_assets.items():
        target = resolve_target(
            catalog,
            source,
            logical_name=f"lang:{code}",
            catalog_key=asset_name,
        )
        downloaded = _download_target(target, downloader=bundle_downloader, root=bundles_root)
        for item in downloaded:
            all_downloads[(item.resolved.bundle_name, item.resolved.cache_hash)] = item
        asset_locations.extend(_bundle_locations(target, downloaded))
        language_payloads[code] = _find_text_asset(downloaded, asset_name)

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
    )
    for item in str_downloaded:
        all_downloads[(item.resolved.bundle_name, item.resolved.cache_hash)] = item
    asset_locations.extend(_bundle_locations(str_target, str_downloaded))

    str_assets: dict[str, bytes] = {}
    for item in str_downloaded:
        str_assets.update(extract_text_assets(item.path, name_prefix=config.str_asset_prefix))
    empty_str_assets = tuple(sorted(name for name, payload in str_assets.items() if not payload))

    tmp_target = resolve_target(
        catalog,
        source,
        logical_name="tmp-font",
        catalog_key=config.tmp_catalog_key,
    )
    tmp_downloaded = _download_target(tmp_target, downloader=bundle_downloader, root=bundles_root)
    for item in tmp_downloaded:
        all_downloads[(item.resolved.bundle_name, item.resolved.cache_hash)] = item
    asset_locations.extend(_bundle_locations(tmp_target, tmp_downloaded))

    lang_units = extract_lang_units(language_payloads)
    str_units = extract_str_units(str_assets, asset_prefix=config.str_asset_prefix)
    units = lang_units + str_units

    return PreparedRevision(
        source=source,
        catalog_hash=catalog_hash,
        catalog=catalog_download,
        units=units,
        asset_locations=tuple(asset_locations),
        downloaded_bundles=tuple(all_downloads.values()),
        empty_str_assets=empty_str_assets,
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
    source_result: SourceSyncResult = sync_revision_sources(conn, revision, prepared.units)
    locations_idempotent = sync_asset_locations(
        conn,
        source_result.revision_id,
        prepared.asset_locations,
    )
    mark_revision_processed(conn, source_result.revision_id)
    return SyncRevisionResult(
        revision_id=str(source_result.revision_id),
        idempotent=source_result.idempotent and locations_idempotent,
        unit_count=len(prepared.units),
        asset_location_count=len(prepared.asset_locations),
        downloaded_bundle_count=len(prepared.downloaded_bundles),
        empty_str_assets=prepared.empty_str_assets,
    )
