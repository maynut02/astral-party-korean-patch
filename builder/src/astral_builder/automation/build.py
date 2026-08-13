from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg

from astral_builder.addressables.catalog import AddressablesCatalog
from astral_builder.addressables.resolver import ResolvedBundle, resolve_target
from astral_builder.automation.sync import RouteSyncConfig, load_route_sync_config
from astral_builder.database.builds import (
    BuildFileRecord,
    begin_build,
    record_build_files,
    set_build_status,
)
from astral_builder.database.repository import load_translation_snapshot
from astral_builder.extract.unity import extract_text_assets
from astral_builder.game.source import GameSource, GameSourceClient
from astral_builder.patch.builder import patch_lang_bundle, patch_str_bundle
from astral_builder.patch.fonts import patch_legacy_font, patch_tmp_font_bundle
from astral_builder.patch.translations import DistributionChannel
from astral_builder.release.manifest import (
    ManifestFile,
    PatchManifest,
    PatchMetadata,
    TargetGame,
    write_manifest,
)
from astral_builder.release.transport import gzip_payload
from astral_builder.source.downloader import DownloadedBundle, RemoteBundleDownloader
from astral_builder.validate.assets import validate_file


@dataclass(frozen=True, slots=True)
class StoredRevision:
    id: UUID
    route: str
    game_version: str
    revision: str
    source_url: str
    catalog_url: str
    catalog_sha256: str
    catalog_hash: str


@dataclass(frozen=True, slots=True)
class StoredBundle:
    logical_name: str
    bundle_name: str
    bundle_hash: str
    source_sha256: str
    source_size: int


@dataclass(frozen=True, slots=True)
class BuiltPatch:
    build_id: UUID
    manifest: Path
    files: tuple[ManifestFile, ...]
    translation_fingerprint: str


def load_stored_revision(conn: psycopg.Connection, revision_id: UUID) -> StoredRevision:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, route, game_version, revision, source_url, catalog_url,
                   catalog_sha256, catalog_build_hash
            FROM game_revisions
            WHERE id = %s AND processed_at IS NOT NULL
            """,
            (revision_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise KeyError(f"processed revision not found: {revision_id}")
    if not row[7]:
        raise RuntimeError("processed revision has no catalog compatibility hash")
    return StoredRevision(*row)


def load_stored_bundles(conn: psycopg.Connection, revision_id: UUID) -> tuple[StoredBundle, ...]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT logical_name, bundle_name, bundle_hash, source_sha256, source_size
            FROM asset_locations
            WHERE revision_id = %s AND origin = 'remote' AND asset_type = 'AssetBundle'
            """,
            (revision_id,),
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        if not all(row[1:5]):
            raise RuntimeError(f"incomplete remote bundle record for {row[0]}")
        result.append(StoredBundle(row[0], row[1], row[2], row[3], int(row[4])))
    return tuple(result)


def _verify_download(
    logical_name: str,
    item: DownloadedBundle,
    stored: tuple[StoredBundle, ...],
) -> None:
    matches = [
        row
        for row in stored
        if row.logical_name == logical_name
        and row.bundle_name == item.resolved.bundle_name
        and row.bundle_hash == item.resolved.cache_hash
    ]
    if len(matches) != 1:
        raise RuntimeError(f"stored bundle identity missing or ambiguous for {logical_name}")
    expected = matches[0]
    if item.sha256 != expected.source_sha256 or item.size != expected.source_size:
        raise RuntimeError(
            f"immutable source bundle changed for {logical_name}: "
            f"expected={expected.source_sha256}/{expected.source_size} "
            f"actual={item.sha256}/{item.size}"
        )


def _download_target(
    logical_name: str,
    catalog: AddressablesCatalog,
    source: GameSource,
    catalog_key: str,
    stored: tuple[StoredBundle, ...],
    downloader: RemoteBundleDownloader,
    root: Path,
) -> tuple[DownloadedBundle, ...]:
    target = resolve_target(catalog, source, logical_name=logical_name, catalog_key=catalog_key)
    items = downloader.download_target(target.bundles, root)
    for item in items:
        _verify_download(logical_name, item, stored)
    return items


def _one_bundle_with_text_asset(items: tuple[DownloadedBundle, ...], name: str) -> DownloadedBundle:
    matches = [item for item in items if name in extract_text_assets(item.path)]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one bundle containing TextAsset {name!r}")
    return matches[0]


def _release_name(prefix: str, bundle: ResolvedBundle) -> str:
    return f"{prefix}-{bundle.bundle_name}-{bundle.cache_hash}.bin"


def _download_url(base: str, name: str) -> str:
    return f"{base.rstrip('/')}/{name}"


def _package_payload(payload: Path, releases: Path, release_name: str) -> Path:
    transport = releases / f"{release_name}.gz"
    gzip_payload(payload, transport)
    return transport


def build_patch(
    conn: psycopg.Connection,
    *,
    revision_id: UUID,
    route_config: str | Path,
    work_dir: str | Path,
    output_dir: str | Path,
    asset_base_url: str,
    patch_version: str,
    channel: DistributionChannel,
    git_commit: str | None = None,
    github_run_id: str | None = None,
    legacy_data_path: str | Path | None,
    resources_root: str | Path | None = None,
    client: GameSourceClient | None = None,
    downloader: RemoteBundleDownloader | None = None,
) -> BuiltPatch:
    config: RouteSyncConfig = load_route_sync_config(route_config)
    revision = load_stored_revision(conn, revision_id)
    if revision.route != config.route:
        raise RuntimeError(f"route mismatch: db={revision.route} config={config.route}")
    snapshot = load_translation_snapshot(conn, revision_id)
    stored = load_stored_bundles(conn, revision_id)
    source = GameSource(
        route=revision.route,
        version=revision.game_version,
        revision=revision.revision,
        source_url=revision.source_url,
        catalog_url=revision.catalog_url,
    )
    source_client = client or GameSourceClient()
    bundle_downloader = downloader or RemoteBundleDownloader()
    work = Path(work_dir)
    output = Path(output_dir)
    releases = output / "assets"
    payloads = work / "payloads"
    releases.mkdir(parents=True, exist_ok=True)
    payloads.mkdir(parents=True, exist_ok=True)

    catalog_download = source_client.download_catalog(
        source, work / "catalog" / f"catalog_{revision.game_version}.json"
    )
    if catalog_download.sha256 != revision.catalog_sha256:
        raise RuntimeError("immutable catalog SHA-256 changed since sync")
    if source_client.fetch_catalog_hash(source) != revision.catalog_hash:
        raise RuntimeError("Addressables catalog compatibility hash changed since sync")
    catalog = AddressablesCatalog.from_path(catalog_download.path)

    manifest_files: list[ManifestFile] = []
    bundles_root = work / "bundles"

    lang_name = config.lang_assets[config.lang_target]
    lang_items = _download_target(
        f"lang:{config.lang_target}",
        catalog,
        source,
        lang_name,
        stored,
        bundle_downloader,
        bundles_root,
    )
    lang_item = _one_bundle_with_text_asset(lang_items, lang_name)
    lang_release_name = _release_name("lang", lang_item.resolved)
    lang_out = payloads / lang_release_name
    patch_lang_bundle(lang_item.path, lang_out, snapshot, asset_name=lang_name, channel=channel)
    validate_file(lang_out)
    lang_transport = _package_payload(lang_out, releases, lang_release_name)
    manifest_files.append(
        ManifestFile.from_paths(
            lang_out,
            lang_transport,
            target="addressables",
            path=lang_item.resolved.cache_relative_path,
            download_url=_download_url(asset_base_url, lang_transport.name),
        )
    )

    str_items = _download_target(
        "str", catalog, source, config.str_catalog_key, stored, bundle_downloader, bundles_root
    )
    str_candidates = [
        item
        for item in str_items
        if extract_text_assets(item.path, name_prefix=config.str_asset_prefix)
    ]
    if len(str_candidates) != 1:
        raise RuntimeError("expected exactly one STR source bundle")
    str_item = str_candidates[0]
    str_release_name = _release_name("str", str_item.resolved)
    str_out = payloads / str_release_name
    patch_str_bundle(
        str_item.path,
        str_out,
        snapshot,
        asset_prefix=config.str_asset_prefix,
        target_field=config.str_target_field,
        channel=channel,
    )
    validate_file(str_out)
    str_transport = _package_payload(str_out, releases, str_release_name)
    manifest_files.append(
        ManifestFile.from_paths(
            str_out,
            str_transport,
            target="addressables",
            path=str_item.resolved.cache_relative_path,
            download_url=_download_url(asset_base_url, str_transport.name),
        )
    )

    tmp_items = _download_target(
        "tmp-font", catalog, source, config.tmp_catalog_key, stored, bundle_downloader, bundles_root
    )
    if len(tmp_items) != 1:
        raise RuntimeError("expected exactly one remote TMP font bundle")
    tmp_item = tmp_items[0]
    resource_root = Path(resources_root or config.resources_root)
    tmp_release_name = _release_name("tmp-font", tmp_item.resolved)
    tmp_out = payloads / tmp_release_name
    patch_tmp_font_bundle(
        tmp_item.path,
        tmp_out,
        mono_name=config.tmp_asset_name,
        mono_payload=(resource_root / "tmp-font.dat").read_bytes(),
        texture_name=f"{config.tmp_asset_name} Atlas",
        atlas_png=(resource_root / "tmp-font-atlas.png").read_bytes(),
    )
    validate_file(tmp_out)
    tmp_transport = _package_payload(tmp_out, releases, tmp_release_name)
    manifest_files.append(
        ManifestFile.from_paths(
            tmp_out,
            tmp_transport,
            target="addressables",
            path=tmp_item.resolved.cache_relative_path,
            download_url=_download_url(asset_base_url, tmp_transport.name),
        )
    )

    if config.legacy_font_name is not None:
        if legacy_data_path is None:
            raise RuntimeError(
                "legacy data.unity3d input is required for this route before a complete patch "
                "manifest can be written"
            )
        legacy_release_name = "game-data-data.unity3d"
        legacy_out = payloads / legacy_release_name
        patch_legacy_font(
            legacy_data_path,
            legacy_out,
            font_name=config.legacy_font_name,
            font_payload=(resource_root / "legacy-font.ttf").read_bytes(),
        )
        validate_file(legacy_out)
        legacy_transport = _package_payload(legacy_out, releases, legacy_release_name)
        manifest_files.append(
            ManifestFile.from_paths(
                legacy_out,
                legacy_transport,
                target="game-data",
                path="data.unity3d",
                download_url=_download_url(asset_base_url, legacy_transport.name),
            )
        )

    build_record = begin_build(
        conn,
        revision_id=revision_id,
        route=revision.route,
        channel=channel.value,
        translation_fingerprint=snapshot.fingerprint,
        git_commit=git_commit,
        github_run_id=github_run_id,
    )

    manifest = PatchManifest(
        patch=PatchMetadata(
            version=patch_version,
            channel=channel.value,
            route=revision.route,
            build_id=str(build_record.id),
            translation_fingerprint=snapshot.fingerprint,
        ),
        game=TargetGame(
            version=revision.game_version,
            revision=revision.revision,
            catalog_hash=revision.catalog_hash,
        ),
        files=tuple(manifest_files),
    )
    manifest_path = write_manifest(manifest, output / "manifest.json")
    build_files = tuple(
        BuildFileRecord(
            target="game_data" if item.target == "game-data" else item.target,
            relative_path=item.path,
            operation=item.operation,
            sha256=item.sha256,
            size=item.size,
        )
        for item in manifest_files
    )
    record_build_files(conn, build_record.id, build_files)
    set_build_status(conn, build_record.id, "validated")
    return BuiltPatch(build_record.id, manifest_path, tuple(manifest_files), snapshot.fingerprint)


def write_build_github_output(result: BuiltPatch, destination: str | Path) -> None:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(f"build_id={result.build_id}\n")
        file.write(f"manifest={result.manifest}\n")
        file.write(f"translation_fingerprint={result.translation_fingerprint}\n")
        file.write(f"file_count={len(result.files)}\n")
