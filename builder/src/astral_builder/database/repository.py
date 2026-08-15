from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import psycopg

from astral_builder.database.sync import (
    ExistingSourceState,
    SourceDisposition,
    SourceSyncPlan,
    plan_source_sync,
)
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


def _source_state_maps(
    conn: psycopg.Connection,
) -> tuple[dict[Identity, UUID], dict[Identity, ExistingSourceState]]:
    """Load all stable unit IDs and the active source state in one round trip."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                tu.kind, tu.namespace, tu.unit_key, tu.id,
                sv.id, sv.source_fingerprint
            FROM translation_units AS tu
            LEFT JOIN source_versions AS sv ON sv.id = tu.current_source_version_id
            """
        )
        unit_ids: dict[Identity, UUID] = {}
        active: dict[Identity, ExistingSourceState] = {}
        for row in cur.fetchall():
            identity = (row[0], row[1], row[2])
            unit_ids[identity] = row[3]
            if row[4] is not None:
                active[identity] = ExistingSourceState(
                    unit_id=row[3],
                    source_version_id=row[4],
                    source_fingerprint=row[5],
                )
        return unit_ids, active


@dataclass(frozen=True, slots=True)
class _SourceStageRow:
    unit_id: UUID
    kind: str
    namespace: str
    unit_key: str
    is_new_unit: bool
    proposed_source_version_id: UUID
    cn_s: str
    en: str
    jp: str
    cn_t: str
    source_fingerprint: str
    old_source_version_id: UUID | None
    change_type: str

    def as_copy_row(self) -> tuple[object, ...]:
        return (
            self.unit_id,
            self.kind,
            self.namespace,
            self.unit_key,
            self.is_new_unit,
            self.proposed_source_version_id,
            self.cn_s,
            self.en,
            self.jp,
            self.cn_t,
            self.source_fingerprint,
            self.old_source_version_id,
            self.change_type,
        )


def _prepare_source_stage_rows(
    plan: SourceSyncPlan,
    unit_ids: dict[Identity, UUID],
) -> tuple[_SourceStageRow, ...]:
    rows: list[_SourceStageRow] = []
    for item in plan.sources:
        if item.disposition is SourceDisposition.UNCHANGED:
            continue
        unit_id = unit_ids.get(item.unit.identity)
        is_new_unit = unit_id is None
        if unit_id is None:
            unit_id = uuid4()
        source = item.unit.source.normalized()
        rows.append(
            _SourceStageRow(
                unit_id=unit_id,
                kind=item.unit.kind.value,
                namespace=item.unit.namespace,
                unit_key=item.unit.key,
                is_new_unit=is_new_unit,
                proposed_source_version_id=uuid4(),
                cn_s=source.cn_s,
                en=source.en,
                jp=source.jp,
                cn_t=source.cn_t,
                source_fingerprint=source.fingerprint,
                old_source_version_id=(
                    None if item.state is None else item.state.source_version_id
                ),
                change_type=(
                    "added" if item.disposition is SourceDisposition.NEW else "modified"
                ),
            )
        )
    return tuple(rows)


def _bulk_persist_source_stage(
    cur: psycopg.Cursor,
    *,
    revision_id: UUID,
    rows: tuple[_SourceStageRow, ...],
    progress: Callable[[str], None],
) -> None:
    if not rows:
        return

    progress(f"database: stage {len(rows)} added/modified source row(s)")
    cur.execute(
        """
        CREATE TEMP TABLE source_sync_stage (
            unit_id uuid NOT NULL,
            kind text NOT NULL,
            namespace text NOT NULL,
            unit_key text NOT NULL,
            is_new_unit boolean NOT NULL,
            proposed_source_version_id uuid NOT NULL,
            cn_s text NOT NULL,
            en text NOT NULL,
            jp text NOT NULL,
            cn_t text NOT NULL,
            source_fingerprint text NOT NULL,
            old_source_version_id uuid,
            change_type text NOT NULL
        ) ON COMMIT DROP
        """
    )
    with cur.copy(
        """
        COPY source_sync_stage(
            unit_id, kind, namespace, unit_key, is_new_unit,
            proposed_source_version_id, cn_s, en, jp, cn_t,
            source_fingerprint, old_source_version_id, change_type
        ) FROM STDIN
        """
    ) as copy:
        for row in rows:
            copy.write_row(row.as_copy_row())

    new_unit_count = sum(row.is_new_unit for row in rows)
    if new_unit_count:
        progress(f"database: insert {new_unit_count} new translation unit(s)")
        cur.execute(
            """
            INSERT INTO translation_units(id, kind, namespace, unit_key)
            SELECT unit_id, kind, namespace, unit_key
            FROM source_sync_stage
            WHERE is_new_unit
            """
        )
        if cur.rowcount != new_unit_count:
            raise RuntimeError(
                "bulk translation unit insert count mismatch: "
                f"expected={new_unit_count} actual={cur.rowcount}"
            )

    progress(f"database: ensure {len(rows)} source version(s)")
    cur.execute(
        """
        INSERT INTO source_versions(
            id, unit_id, cn_s, en, jp, cn_t,
            source_fingerprint, first_seen_revision_id
        )
        SELECT
            proposed_source_version_id, unit_id, cn_s, en, jp, cn_t,
            source_fingerprint, %s
        FROM source_sync_stage
        WHERE true
        ON CONFLICT (unit_id, source_fingerprint) DO NOTHING
        """,
        (revision_id,),
    )

    progress(f"database: record {len(rows)} applied source change(s)")
    cur.execute(
        """
        INSERT INTO source_changes(
            revision_id, unit_id, change_type,
            old_source_version_id, new_source_version_id
        )
        SELECT
            %s, stage.unit_id, stage.change_type,
            stage.old_source_version_id, versions.id
        FROM source_sync_stage AS stage
        JOIN source_versions AS versions
          ON versions.unit_id = stage.unit_id
         AND versions.source_fingerprint = stage.source_fingerprint
        """,
        (revision_id,),
    )
    if cur.rowcount != len(rows):
        raise RuntimeError(
            "bulk source change insert count mismatch: "
            f"expected={len(rows)} actual={cur.rowcount}"
        )

    progress(f"database: update {len(rows)} current source pointer(s)")
    cur.execute(
        """
        UPDATE translation_units AS units
        SET current_source_version_id = versions.id
        FROM source_sync_stage AS stage
        JOIN source_versions AS versions
          ON versions.unit_id = stage.unit_id
         AND versions.source_fingerprint = stage.source_fingerprint
        WHERE units.id = stage.unit_id
        """
    )
    if cur.rowcount != len(rows):
        raise RuntimeError(
            "bulk current source update count mismatch: "
            f"expected={len(rows)} actual={cur.rowcount}"
        )
    cur.execute("DROP TABLE source_sync_stage")


def _bulk_persist_removed_sources(
    cur: psycopg.Cursor,
    *,
    revision_id: UUID,
    removed: tuple[ExistingSourceState, ...],
    progress: Callable[[str], None],
) -> None:
    if not removed:
        return

    progress(f"database: stage {len(removed)} removed source row(s)")
    cur.execute(
        """
        CREATE TEMP TABLE source_remove_stage (
            unit_id uuid PRIMARY KEY,
            old_source_version_id uuid NOT NULL
        ) ON COMMIT DROP
        """
    )
    with cur.copy(
        "COPY source_remove_stage(unit_id, old_source_version_id) FROM STDIN"
    ) as copy:
        for state in removed:
            copy.write_row((state.unit_id, state.source_version_id))

    cur.execute(
        """
        INSERT INTO source_changes(
            revision_id, unit_id, change_type,
            old_source_version_id, new_source_version_id
        )
        SELECT %s, unit_id, 'removed', old_source_version_id, NULL
        FROM source_remove_stage
        """,
        (revision_id,),
    )
    if cur.rowcount != len(removed):
        raise RuntimeError(
            "bulk removed source change count mismatch: "
            f"expected={len(removed)} actual={cur.rowcount}"
        )

    progress(f"database: clear {len(removed)} removed source pointer(s)")
    cur.execute(
        """
        UPDATE translation_units AS units
        SET current_source_version_id = NULL
        FROM source_remove_stage AS removed
        WHERE units.id = removed.unit_id
          AND units.current_source_version_id = removed.old_source_version_id
        """
    )
    if cur.rowcount != len(removed):
        raise RuntimeError(
            "bulk removed source pointer count mismatch: "
            f"expected={len(removed)} actual={cur.rowcount}"
        )
    cur.execute("DROP TABLE source_remove_stage")


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
    *,
    progress: Callable[[str], None] | None = None,
) -> SourceSyncResult:
    """Apply one canonical source scan using set-based PostgreSQL writes.

    One game revision is the source-change group. Added/modified/removed units are staged with
    PostgreSQL COPY and applied with a fixed number of set-based statements, avoiding one network
    round trip per translation unit. Historical source versions are reused by the
    ``(unit_id, source_fingerprint)`` uniqueness constraint.
    """
    if progress is None:
        def no_progress(_message: str) -> None:
            return None

        progress = no_progress

    with conn.transaction():
        revision_id, existed = _ensure_revision(conn, revision)
        unit_ids, existing = _source_state_maps(conn)
        plan = plan_source_sync(units, existing)
        progress(
            "database: source plan "
            f"+{plan.new_count} ~{plan.changed_count} "
            f"={plan.unchanged_count} -{plan.removed_count}"
        )

        staged = _prepare_source_stage_rows(plan, unit_ids)
        with conn.cursor() as cur:
            _bulk_persist_source_stage(
                cur,
                revision_id=revision_id,
                rows=staged,
                progress=progress,
            )
            _bulk_persist_removed_sources(
                cur,
                revision_id=revision_id,
                removed=plan.removed,
                progress=progress,
            )

        progress("database: canonical source persistence complete")
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
