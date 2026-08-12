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
        return SourceSyncResult(revision_id=revision_id, plan=plan)


def mark_revision_processed(conn: psycopg.Connection, revision_id: UUID) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE game_revisions SET processed_at = COALESCE(processed_at, now()) WHERE id = %s",
            (revision_id,),
        )
        if cur.rowcount != 1:
            raise KeyError(f"game revision not found: {revision_id}")


def load_translation_snapshot(
    conn: psycopg.Connection,
    revision_id: UUID,
    *,
    locale: str = "ko",
):
    from astral_builder.database.snapshot import SnapshotUnit, make_snapshot
    from astral_builder.formats.model import SourceStrings

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT route, game_version, revision
            FROM game_revisions
            WHERE id = %s AND processed_at IS NOT NULL
            """,
            (revision_id,),
        )
        revision_row = cur.fetchone()
        if revision_row is None:
            raise KeyError(f"processed game revision not found: {revision_id}")

        cur.execute(
            """
            SELECT
                tu.kind, tu.namespace, tu.unit_key,
                st.cn_s, st.en, st.jp, st.cn_t, st.source_fingerprint,
                COALESCE(tr.text, ''), tr.status, tr.source_fingerprint
            FROM source_texts st
            JOIN translation_units tu ON tu.id = st.unit_id
            LEFT JOIN translations tr ON tr.unit_id = tu.id AND tr.locale = %s
            WHERE st.revision_id = %s
            ORDER BY tu.kind, tu.namespace, tu.unit_key
            """,
            (locale, revision_id),
        )
        units = tuple(
            SnapshotUnit(
                kind=row[0],
                namespace=row[1],
                key=row[2],
                source=SourceStrings(cn_s=row[3], en=row[4], jp=row[5], cn_t=row[6]),
                source_fingerprint=row[7],
                translation=row[8],
                translation_status=row[9],
                translation_source_fingerprint=row[10],
            )
            for row in cur.fetchall()
        )

    return make_snapshot(
        revision_id=str(revision_id),
        route=revision_row[0],
        game_version=revision_row[1],
        revision=revision_row[2],
        locale=locale,
        units=units,
    )


@dataclass(frozen=True, slots=True)
class AssetLocationInput:
    logical_name: str
    catalog_key: str
    origin: str
    asset_type: str
    asset_name: str
    bundle_name: str | None = None
    bundle_hash: str | None = None
    cache_root: str | None = None
    source_sha256: str | None = None
    source_size: int | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.logical_name, self.asset_type, self.asset_name)

    def validate(self) -> None:
        if self.origin not in {"remote", "runtime", "game_data"}:
            raise ValueError(f"unsupported asset origin: {self.origin}")
        if self.source_size is not None and self.source_size < 0:
            raise ValueError("source_size cannot be negative")
        if self.source_sha256 is not None:
            if len(self.source_sha256) != 64 or any(
                char not in "0123456789abcdef" for char in self.source_sha256
            ):
                raise ValueError("source_sha256 must be lowercase SHA-256 hex")


def _asset_location_map(
    locations: tuple[AssetLocationInput, ...],
) -> dict[tuple[str, str, str], AssetLocationInput]:
    result: dict[tuple[str, str, str], AssetLocationInput] = {}
    for location in locations:
        location.validate()
        if location.identity in result:
            raise ValueError(f"duplicate asset location identity: {location.identity}")
        result[location.identity] = location
    return result


def sync_asset_locations(
    conn: psycopg.Connection,
    revision_id: UUID,
    locations: tuple[AssetLocationInput, ...],
) -> bool:
    """Persist immutable asset locations. Returns True when the call is idempotent."""
    incoming = _asset_location_map(locations)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            SELECT logical_name, catalog_key, origin, asset_type, asset_name,
                   bundle_name, bundle_hash, cache_root, source_sha256, source_size
            FROM asset_locations
            WHERE revision_id = %s
            """,
            (revision_id,),
        )
        rows = cur.fetchall()
        if rows:
            persisted = {
                (row[0], row[3], row[4]): AssetLocationInput(
                    logical_name=row[0],
                    catalog_key=row[1],
                    origin=row[2],
                    asset_type=row[3],
                    asset_name=row[4],
                    bundle_name=row[5],
                    bundle_hash=row[6],
                    cache_root=row[7],
                    source_sha256=row[8],
                    source_size=row[9],
                )
                for row in rows
            }
            if persisted != incoming:
                raise RevisionConflictError("asset locations differ for an existing revision")
            return True

        insert_rows = [
            (
                uuid4(),
                revision_id,
                item.logical_name,
                item.catalog_key,
                item.origin,
                item.bundle_name,
                item.bundle_hash,
                item.cache_root,
                item.asset_type,
                item.asset_name,
                item.source_sha256,
                item.source_size,
            )
            for item in locations
        ]
        if insert_rows:
            cur.executemany(
                """
                INSERT INTO asset_locations(
                    id, revision_id, logical_name, catalog_key, origin,
                    bundle_name, bundle_hash, cache_root,
                    asset_type, asset_name, source_sha256, source_size
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                insert_rows,
            )
        return False
