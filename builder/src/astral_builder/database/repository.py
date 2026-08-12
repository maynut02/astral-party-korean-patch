from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import psycopg

from astral_builder.database.sync import ExistingSourceState, SourceSyncPlan, plan_source_sync
from astral_builder.formats.model import TranslationUnit

Identity = tuple[str, str, str]


class RevisionConflictError(RuntimeError):
    """Raised when an already persisted immutable revision has different content."""


@dataclass(frozen=True, slots=True)
class RevisionInput:
    route: str
    game_version: str
    revision: str
    source_url: str
    catalog_url: str
    catalog_sha256: str
    catalog_build_hash: str | None = None
    detected_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SourceSyncResult:
    revision_id: UUID
    plan: SourceSyncPlan
    idempotent: bool = False


def _source_map(units: tuple[TranslationUnit, ...]) -> dict[Identity, str]:
    result: dict[Identity, str] = {}
    for unit in units:
        if unit.identity in result:
            raise ValueError(f"duplicate translation unit identity: {unit.identity}")
        result[unit.identity] = unit.source.fingerprint
    return result


def assert_idempotent_source_match(
    units: tuple[TranslationUnit, ...],
    persisted: dict[Identity, str],
) -> None:
    incoming = _source_map(units)
    if incoming != persisted:
        added = sorted(set(incoming) - set(persisted))
        removed = sorted(set(persisted) - set(incoming))
        changed = sorted(
            identity
            for identity in set(incoming) & set(persisted)
            if incoming[identity] != persisted[identity]
        )
        raise RevisionConflictError(
            "existing revision source set differs from incoming data: "
            f"added={added[:10]} removed={removed[:10]} changed={changed[:10]}"
        )


def _ensure_revision(conn: psycopg.Connection, revision: RevisionInput) -> tuple[UUID, bool]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_url, catalog_url, catalog_sha256, catalog_build_hash
            FROM game_revisions
            WHERE route = %s AND game_version = %s AND revision = %s
            """,
            (revision.route, revision.game_version, revision.revision),
        )
        row = cur.fetchone()
        if row is not None:
            revision_id = row[0]
            existing = (row[1], row[2], row[3], row[4])
            incoming = (
                revision.source_url,
                revision.catalog_url,
                revision.catalog_sha256,
                revision.catalog_build_hash,
            )
            if existing != incoming:
                raise RevisionConflictError(
                    f"immutable revision metadata changed for "
                    f"{revision.route}/{revision.game_version}/{revision.revision}"
                )
            return revision_id, True

        revision_id = uuid4()
        cur.execute(
            """
            INSERT INTO game_revisions(
                id, route, game_version, revision, source_url, catalog_url,
                catalog_sha256, catalog_build_hash, detected_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
            """,
            (
                revision_id,
                revision.route,
                revision.game_version,
                revision.revision,
                revision.source_url,
                revision.catalog_url,
                revision.catalog_sha256,
                revision.catalog_build_hash,
                revision.detected_at,
            ),
        )
        return revision_id, False


def _current_revision_sources(
    conn: psycopg.Connection,
    revision_id: UUID,
) -> dict[Identity, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tu.kind, tu.namespace, tu.unit_key, st.source_fingerprint
            FROM source_texts st
            JOIN translation_units tu ON tu.id = st.unit_id
            WHERE st.revision_id = %s
            """,
            (revision_id,),
        )
        return {(row[0], row[1], row[2]): row[3] for row in cur.fetchall()}


def _latest_source_states(
    conn: psycopg.Connection,
    *,
    route: str,
    exclude_revision_id: UUID,
) -> dict[Identity, ExistingSourceState]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (tu.id)
                tu.kind, tu.namespace, tu.unit_key, tu.id, st.source_fingerprint
            FROM translation_units tu
            JOIN source_texts st ON st.unit_id = tu.id
            JOIN game_revisions gr ON gr.id = st.revision_id
            WHERE gr.route = %s AND gr.id <> %s
            ORDER BY tu.id, gr.detected_at DESC, gr.id DESC
            """,
            (route, exclude_revision_id),
        )
        return {
            (row[0], row[1], row[2]): ExistingSourceState(str(row[3]), row[4])
            for row in cur.fetchall()
        }


def _all_unit_ids(conn: psycopg.Connection) -> dict[Identity, UUID]:
    with conn.cursor() as cur:
        cur.execute("SELECT kind, namespace, unit_key, id FROM translation_units")
        return {(row[0], row[1], row[2]): row[3] for row in cur.fetchall()}


def sync_revision_sources(
    conn: psycopg.Connection,
    revision: RevisionInput,
    units: tuple[TranslationUnit, ...],
) -> SourceSyncResult:
    """Persist one complete source snapshot without modifying translations."""
    with conn.transaction():
        revision_id, existed = _ensure_revision(conn, revision)
        current = _current_revision_sources(conn, revision_id)
        if current:
            assert_idempotent_source_match(units, current)
            existing = _latest_source_states(
                conn,
                route=revision.route,
                exclude_revision_id=revision_id,
            )
            return SourceSyncResult(
                revision_id=revision_id,
                plan=plan_source_sync(units, existing),
                idempotent=True,
            )
        if existed:
            # A revision row with no source rows can only be a safely retryable pre-sync state.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT processed_at FROM game_revisions WHERE id = %s",
                    (revision_id,),
                )
                processed_at = cur.fetchone()[0]
            if processed_at is not None:
                raise RevisionConflictError("processed revision unexpectedly has no source rows")

        existing = _latest_source_states(
            conn,
            route=revision.route,
            exclude_revision_id=revision_id,
        )
        plan = plan_source_sync(units, existing)

        unit_ids = _all_unit_ids(conn)
        missing_units = [unit for unit in units if unit.identity not in unit_ids]
        if missing_units:
            rows = [
                (uuid4(), unit.kind.value, unit.namespace, unit.key)
                for unit in missing_units
            ]
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO translation_units(id, kind, namespace, unit_key)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (kind, namespace, unit_key) DO NOTHING
                    """,
                    rows,
                )
            unit_ids = _all_unit_ids(conn)

        source_rows = []
        for unit in units:
            source = unit.source.normalized()
            source_rows.append(
                (
                    revision_id,
                    unit_ids[unit.identity],
                    source.cn_s,
                    source.en,
                    source.jp,
                    source.cn_t,
                    source.fingerprint,
                )
            )
        if source_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO source_texts(
                        revision_id, unit_id, cn_s, en, jp, cn_t, source_fingerprint
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    source_rows,
                )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE game_revisions SET processed_at = now() WHERE id = %s",
                (revision_id,),
            )
        return SourceSyncResult(revision_id=revision_id, plan=plan)
