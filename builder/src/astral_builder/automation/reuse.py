from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg

from astral_builder.automation.release import read_release_metadata


@dataclass(frozen=True, slots=True)
class ReusedRevision:
    route: str
    game_version: str
    revision: str
    revision_id: str
    catalog_hash: str
    source_url: str
    catalog_url: str


def resolve_released_revision(
    conn: psycopg.Connection,
    *,
    manifest_path: str | Path,
    expected_route: str,
) -> ReusedRevision:
    metadata = read_release_metadata(manifest_path)
    if metadata.route != expected_route:
        raise ValueError(
            f"release manifest route mismatch: expected={expected_route} actual={metadata.route}"
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, catalog_build_hash, source_url, catalog_url, processed_at
            FROM game_revisions
            WHERE route = %s AND game_version = %s AND revision = %s
            """,
            (metadata.route, metadata.game_version, metadata.revision),
        )
        row = cur.fetchone()

    if row is None:
        raise KeyError(
            "released game revision is missing from the database: "
            f"{metadata.route}/{metadata.game_version}/{metadata.revision}"
        )
    revision_id, catalog_hash, source_url, catalog_url, processed_at = row
    if processed_at is None:
        raise RuntimeError(
            "released game revision is not fully processed: "
            f"{metadata.route}/{metadata.game_version}/{metadata.revision}"
        )
    if catalog_hash != metadata.catalog_hash:
        raise RuntimeError(
            "released manifest catalog hash differs from the database: "
            f"{metadata.route}/{metadata.game_version}/{metadata.revision}"
        )

    return ReusedRevision(
        route=metadata.route,
        game_version=metadata.game_version,
        revision=metadata.revision,
        revision_id=str(revision_id),
        catalog_hash=str(catalog_hash),
        source_url=str(source_url),
        catalog_url=str(catalog_url),
    )


def write_github_output(state: ReusedRevision, destination: str | Path) -> None:
    lines = (
        "changed=false",
        "sync_required=false",
        f"revision_id={state.revision_id}",
        f"route={state.route}",
        f"game_version={state.game_version}",
        f"revision={state.revision}",
        f"catalog_hash={state.catalog_hash}",
        f"source_url={state.source_url}",
        f"catalog_url={state.catalog_url}",
    )
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
