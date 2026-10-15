from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg

from astral_builder.addressables.catalog import AddressablesCatalog
from astral_builder.addressables.resolver import BundleOrigin, resolve_target
from astral_builder.automation.build import load_stored_revision
from astral_builder.automation.sync import load_route_sync_config
from astral_builder.game.source import GameSource, GameSourceClient
from astral_builder.source.runtime import runtime_bundle_relative_path

_STEAM_GAME_DATA_PATHS = {
    "INT_STEAM": "8vJXnINT/AstralParty_INT_Data",
    "CN_STEAM": "8vJXn6CN/AstralParty_CN_Data",
}


@dataclass(frozen=True, slots=True)
class SteamInputPlan:
    """JSON-ready Steam inputs for one immutable revision.

    game_data_path and filelist are depot-relative. runtime_bundles are relative
    to that game data directory's StreamingAssets/aa directory.
    """

    route: str
    game_version: str
    revision: str
    game_data_path: str
    filelist: tuple[str, ...]
    runtime_bundles: tuple[str, ...]
    catalog_sha256: str
    catalog_hash: str


def plan_steam_inputs(
    conn: psycopg.Connection,
    *,
    revision_id: UUID,
    route_config: str | Path,
    work_dir: str | Path,
    filelist_path: str | Path,
    client: GameSourceClient | None = None,
) -> SteamInputPlan:
    """Write one route's DepotDownloader filelist using its stored catalog only."""
    config = load_route_sync_config(route_config)
    if config.route not in _STEAM_GAME_DATA_PATHS or config.platform != "windows":
        raise ValueError(f"Steam input planning requires a Windows Steam route: {config.route}")
    revision = load_stored_revision(conn, revision_id)
    if revision.route != config.route:
        raise RuntimeError(f"route mismatch: db={revision.route} config={config.route}")

    # Do not discover a current source or reconstruct its CDN URL. Stored URLs
    # may carry a route-specific prefix, suffix, or authentication query.
    source = GameSource(
        route=revision.route,
        version=revision.game_version,
        revision=revision.revision,
        source_url=revision.source_url,
        catalog_url=revision.catalog_url,
    )
    source_client = client or GameSourceClient()
    catalog_download = source_client.download_catalog(
        source,
        Path(work_dir) / revision.route.lower() / "catalog" / "catalog.json",
    )
    if catalog_download.sha256 != revision.catalog_sha256:
        raise RuntimeError("immutable catalog SHA-256 changed since sync")
    if source_client.fetch_catalog_hash(source) != revision.catalog_hash:
        raise RuntimeError("Addressables catalog compatibility hash changed since sync")
    catalog = AddressablesCatalog.from_path(catalog_download.path)
    target = resolve_target(
        catalog,
        source,
        logical_name="tmp-font",
        catalog_key=config.tmp_catalog_key,
    )
    runtime_bundles = tuple(
        sorted(
            {
                runtime_bundle_relative_path(bundle)
                for bundle in target.bundles
                if bundle.origin is BundleOrigin.RUNTIME
            }
        )
    )
    game_data_path = _STEAM_GAME_DATA_PATHS[revision.route]
    filelist = (
        f"{game_data_path}/data.unity3d",
        *(f"{game_data_path}/StreamingAssets/aa/{relative}" for relative in runtime_bundles),
    )
    plan = SteamInputPlan(
        route=revision.route,
        game_version=revision.game_version,
        revision=revision.revision,
        game_data_path=game_data_path,
        filelist=filelist,
        runtime_bundles=runtime_bundles,
        catalog_sha256=revision.catalog_sha256,
        catalog_hash=revision.catalog_hash,
    )
    destination = Path(filelist_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(plan.filelist) + "\n", encoding="utf-8", newline="\n")
    return plan
