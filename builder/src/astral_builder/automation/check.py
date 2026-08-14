from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import psycopg

from astral_builder.database.repository import RevisionConflictError
from astral_builder.game.source import GameSource, GameSourceClient


@dataclass(frozen=True, slots=True)
class RevisionCheck:
    source: GameSource
    catalog_hash: str
    changed: bool


def check_revision(
    conn: psycopg.Connection,
    *,
    route: str,
    game_version: str,
    client: GameSourceClient | None = None,
) -> RevisionCheck:
    source_client = client or GameSourceClient()
    source = source_client.discover(route, game_version)
    catalog_hash = source_client.fetch_catalog_hash(source)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                gr.catalog_build_hash,
                gr.processed_at,
                EXISTS (
                    SELECT 1
                    FROM builds AS b
                    WHERE b.revision_id = gr.id
                      AND b.channel IN ('develop', 'release')
                      AND b.status = 'released'
                ) AS has_published_build
            FROM game_revisions AS gr
            WHERE gr.route = %s AND gr.game_version = %s AND gr.revision = %s
            """,
            (source.route, source.version, source.revision),
        )
        row = cur.fetchone()

    if row is None:
        return RevisionCheck(source=source, catalog_hash=catalog_hash, changed=True)

    persisted_hash, processed_at, has_published_build = row
    if persisted_hash != catalog_hash:
        raise RevisionConflictError(
            "remote catalog hash changed for an existing immutable revision: "
            f"{source.route}/{source.version}/{source.revision} "
            f"db={persisted_hash!r} remote={catalog_hash!r}"
        )
    return RevisionCheck(
        source=source,
        catalog_hash=catalog_hash,
        changed=processed_at is None or not has_published_build,
    )


def write_github_output(check: RevisionCheck, destination: str | Path | TextIO) -> None:
    lines = (
        f"changed={'true' if check.changed else 'false'}",
        f"route={check.source.route}",
        f"game_version={check.source.version}",
        f"revision={check.source.revision}",
        f"catalog_hash={check.catalog_hash}",
        f"source_url={check.source.source_url}",
        f"catalog_url={check.source.catalog_url}",
    )
    payload = "\n".join(lines) + "\n"
    if hasattr(destination, "write"):
        destination.write(payload)  # type: ignore[union-attr]
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(payload)
