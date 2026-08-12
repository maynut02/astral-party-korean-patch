from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from enum import StrEnum

from astral_builder.addressables.catalog import (
    AddressablesCatalog,
    AssetBundleRequestOptions,
    CatalogLocation,
)
from astral_builder.game.source import GameSource


class BundleOrigin(StrEnum):
    REMOTE = "remote"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class ResolvedBundle:
    origin: BundleOrigin
    primary_key: str
    internal_id: str
    bundle_name: str
    cache_hash: str
    expected_size: int
    download_url: str | None
    cache_relative_path: str


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    logical_name: str
    catalog_key: str
    bundles: tuple[ResolvedBundle, ...]


def _normalize_internal_id(value: str) -> str:
    return value.replace("\\\\", "/").replace("\\", "/")


def _bundle_options(location: CatalogLocation) -> AssetBundleRequestOptions:
    options = location.bundle_options
    if options is None:
        raise ValueError(
            "bundle location has no AssetBundleRequestOptions: "
            f"{location.primary_key}"
        )
    if not options.bundle_name or not options.hash:
        raise ValueError(f"bundle options are incomplete: {location.primary_key}")
    return options


def resolve_bundle(location: CatalogLocation, source: GameSource) -> ResolvedBundle:
    if not location.is_asset_bundle:
        raise ValueError(f"location is not an AssetBundle: {location.primary_key}")

    options = _bundle_options(location)
    internal_id = _normalize_internal_id(location.internal_id)
    remote_prefix = "{App.WebServerConfig.Path}/"
    runtime_prefix = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/"

    if internal_id.startswith(remote_prefix):
        relative = internal_id[len(remote_prefix) :].lstrip("/")
        origin = BundleOrigin.REMOTE
        download_url = urllib.parse.urljoin(f"{source.source_url}/", relative)
    elif internal_id.startswith(runtime_prefix):
        relative = internal_id[len(runtime_prefix) :].lstrip("/")
        origin = BundleOrigin.RUNTIME
        download_url = None
    else:
        raise ValueError(f"unsupported Addressables bundle internal id: {location.internal_id}")

    cache_path = f"{options.bundle_name}/{options.hash}/__data"
    return ResolvedBundle(
        origin=origin,
        primary_key=str(location.primary_key),
        internal_id=location.internal_id,
        bundle_name=options.bundle_name,
        cache_hash=options.hash,
        expected_size=options.bundle_size,
        download_url=download_url,
        cache_relative_path=cache_path,
    )


def resolve_target(
    catalog: AddressablesCatalog,
    source: GameSource,
    *,
    logical_name: str,
    catalog_key: str,
) -> ResolvedTarget:
    locations = catalog.locate(catalog_key)
    if not locations:
        raise KeyError(f"catalog key not found: {catalog_key}")
    bundle_locations = catalog.bundle_dependencies(catalog_key)
    if not bundle_locations:
        raise ValueError(f"catalog key has no AssetBundle dependencies: {catalog_key}")
    bundles = tuple(resolve_bundle(location, source) for location in bundle_locations)
    return ResolvedTarget(logical_name=logical_name, catalog_key=catalog_key, bundles=bundles)
