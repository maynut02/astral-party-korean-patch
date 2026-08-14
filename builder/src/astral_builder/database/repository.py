from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import psycopg

from astral_builder.database.sync import ExistingSourceState, SourceSyncPlan, plan_source_sync
from astral_builder.formats.model import TranslationUnit

Identity = tuple[str, str, str]


class RevisionConflictError(RuntimeError):
    """Raised when already persisted immutable revision metadata changes."""


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
                    "immutable revision metadata changed for "
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


def _active_source_states(conn: psycopg.Connection) -> dict[Identity, ExistingSourceState]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                tu.kind, tu.namespace, tu.unit_key,
                tu.id, sv.id, sv.source_fingerprint
            FROM translation_units tu
            JOIN source_versions sv ON sv.id = tu.current_source_version_id
            """
        )
        return {
            (row[0], row[1], row[2]): ExistingSourceState(
                unit_id=row[3],
                source_version_id=row[4],
                source_fingerprint=row[5],
            )
            for row in cur.fetchall()
        }


def _all_unit_ids(conn: psycopg.Connection) -> dict[Identity, UUID]:
    with conn.cursor() as cur:
        cur.execute("SELECT kind, namespace, unit_key, id FROM translation_units")
        return {(row[0], row[1], row[2]): row[3] for row in cur.fetchall()}


def _ensure_source_version(
    conn: psycopg.Connection,
    *,
    unit_id: UUID,
    revision_id: UUID,
    unit: TranslationUnit,
) -> UUID:
    source = unit.source.normalized()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM source_versions
            WHERE unit_id = %s AND source_fingerprint = %s
            """,
            (unit_id, source.fingerprint),
        )
        row = cur.fetchone()
        if row is not None:
            return row[0]
        source_version_id = uuid4()
        cur.execute(
            """
            INSERT INTO source_versions(
                id, unit_id, cn_s, en, jp, cn_t, source_fingerprint, first_seen_revision_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                source_version_id,
                unit_id,
                source.cn_s,
                source.en,
                source.jp,
                source.cn_t,
                source.fingerprint,
                revision_id,
            ),
        )
    return source_version_id


def sync_revision_metadata(
    conn: psycopg.Connection,
    revision: RevisionInput,
) -> tuple[UUID, bool]:
    """Persist immutable route compatibility metadata without translation source data."""
    with conn.transaction():
        revision_id, existed = _ensure_revision(conn, revision)
    return revision_id, existed


def sync_revision_sources(
    conn: psycopg.Connection,
    revision: RevisionInput,
    units: tuple[TranslationUnit, ...],
) -> SourceSyncResult:
    """Apply an INT_STEAM source scan and persist only actual source changes.

    The game revision is the change group. New source text versions are inserted only for
    added/modified units; unchanged text is represented by the existing current pointer.
    Removed units clear their current source pointer but keep all historical versions.
    """
    with conn.transaction():
        revision_id, existed = _ensure_revision(conn, revision)
        existing = _active_source_states(conn)
        plan = plan_source_sync(units, existing)

        unit_ids = _all_unit_ids(conn)
        missing_units = [item.unit for item in plan.sources if item.unit.identity not in unit_ids]
        if missing_units:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO translation_units(id, kind, namespace, unit_key)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (kind, namespace, unit_key) DO NOTHING
                    """,
                    [
                        (uuid4(), unit.kind.value, unit.namespace, unit.key)
                        for unit in missing_units
                    ],
                )
            unit_ids = _all_unit_ids(conn)

        with conn.cursor() as cur:
            for item in plan.sources:
                if item.disposition.value == "unchanged":
                    continue
                unit_id = unit_ids[item.unit.identity]
                new_source_version_id = _ensure_source_version(
                    conn,
                    unit_id=unit_id,
                    revision_id=revision_id,
                    unit=item.unit,
                )
                old_source_version_id = (
                    None if item.state is None else item.state.source_version_id
                )
                change_type = "added" if item.state is None else "modified"
                cur.execute(
                    """
                    INSERT INTO source_changes(
                        revision_id, unit_id, change_type,
                        old_source_version_id, new_source_version_id
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        revision_id,
                        unit_id,
                        change_type,
                        old_source_version_id,
                        new_source_version_id,
                    ),
                )
                cur.execute(
                    "UPDATE translation_units SET current_source_version_id = %s WHERE id = %s",
                    (new_source_version_id, unit_id),
                )

            for state in plan.removed:
                cur.execute(
                    """
                    INSERT INTO source_changes(
                        revision_id, unit_id, change_type,
                        old_source_version_id, new_source_version_id
                    )
                    VALUES (%s, %s, 'removed', %s, NULL)
                    """,
                    (revision_id, state.unit_id, state.source_version_id),
                )
                cur.execute(
                    "UPDATE translation_units SET current_source_version_id = NULL WHERE id = %s",
                    (state.unit_id,),
                )

        return SourceSyncResult(
            revision_id=revision_id,
            plan=plan,
            idempotent=existed
            and plan.new_count == 0
            and plan.changed_count == 0
            and plan.removed_count == 0,
        )


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
    """Load the current canonical source plus current approved production translations."""
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
            SELECT id
            FROM game_revisions
            WHERE route = %s AND processed_at IS NOT NULL
            ORDER BY detected_at DESC, processed_at DESC, id DESC
            LIMIT 1
            """,
            (revision_row[0],),
        )
        latest_row = cur.fetchone()
        if latest_row is None or latest_row[0] != revision_id:
            raise RuntimeError(
                "translation snapshots may only be built from the latest canonical revision"
            )

        cur.execute(
            """
            SELECT
                src.kind, src.namespace, src.unit_key,
                src.source_version_id,
                src.cn_s, src.en, src.jp, src.cn_t,
                COALESCE(tr.text, '')
            FROM current_source_texts src
            LEFT JOIN translations tr
                ON tr.unit_id = src.unit_id AND tr.locale = %s
            ORDER BY src.kind, src.namespace, src.unit_key
            """,
            (locale,),
        )
        units = tuple(
            SnapshotUnit(
                kind=row[0],
                namespace=row[1],
                key=row[2],
                source_version_id=str(row[3]),
                source=SourceStrings(cn_s=row[4], en=row[5], jp=row[6], cn_t=row[7]),
                translation=row[8],
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


def load_latest_translation_snapshot(
    conn: psycopg.Connection,
    *,
    route: str,
    game_version: str,
    locale: str = "ko",
):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM game_revisions
            WHERE route = %s
              AND game_version = %s
              AND processed_at IS NOT NULL
            ORDER BY detected_at DESC, processed_at DESC, id DESC
            LIMIT 1
            """,
            (route, game_version),
        )
        row = cur.fetchone()
    if row is None:
        raise KeyError(f"canonical translation revision not found: {route}/{game_version}")
    return load_translation_snapshot(conn, row[0], locale=locale)


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
