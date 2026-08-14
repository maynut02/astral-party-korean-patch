#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg

SCHEMA_VERSION = 1
DEFAULT_ACTOR = "database-reset-restore"


def _database_url(explicit: str) -> str:
    value = explicit.strip() or os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("--database-url or DATABASE_URL is required")
    return value


def _has_column(conn: psycopg.Connection, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = %s
                  AND column_name = %s
            )
            """,
            (table, column),
        )
        return bool(cur.fetchone()[0])


def export_translations(database_url: str, output: Path) -> int:
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        if _has_column(conn, "translations", "status"):
            # Pre-reset schema: preserve only values that were actually approved. If several
            # source-fingerprint variants exist, the most recently approved value wins.
            cur.execute(
                """
                SELECT DISTINCT ON (tu.id, tr.locale)
                    tu.kind, tu.namespace, tu.unit_key, tr.locale, tr.text
                FROM translations tr
                JOIN translation_units tu ON tu.id = tr.unit_id
                WHERE tr.status = 'approved'
                  AND btrim(tr.text) <> ''
                  AND COALESCE(tr.updated_by, '') NOT IN (
                      'one-shot-neon-approve', 'synthetic-approval-cleanup'
                  )
                ORDER BY tu.id, tr.locale, tr.updated_at DESC, tr.id DESC
                """
            )
        else:
            # New schema: every row in translations is already approved production data.
            cur.execute(
                """
                SELECT tu.kind, tu.namespace, tu.unit_key, tr.locale, tr.text
                FROM translations tr
                JOIN translation_units tu ON tu.id = tr.unit_id
                ORDER BY tu.kind, tu.namespace, tu.unit_key, tr.locale
                """
            )
        rows = cur.fetchall()

    items = [
        {
            "kind": row[0],
            "namespace": row[1],
            "key": row[2],
            "locale": row[3],
            "text": row[4],
        }
        for row in rows
    ]
    items.sort(key=lambda item: (item["kind"], item["namespace"], item["key"], item["locale"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"schemaVersion": SCHEMA_VERSION, "translations": items}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return len(items)


def import_translations(database_url: str, source: Path, actor: str) -> tuple[int, int, int]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("unsupported translation backup schema")
    items = payload.get("translations")
    if not isinstance(items, list):
        raise TypeError("translation backup is missing translations array")

    restored = skipped_existing = missing_unit = 0
    with psycopg.connect(database_url) as conn, conn.transaction(), conn.cursor() as cur:
        group_id = uuid4()
        cur.execute(
            """
            INSERT INTO translation_change_groups(id, title, description, created_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                group_id,
                "Database reset translation restore",
                "Approved translations restored after a full database reset.",
                actor,
            ),
        )

        for item in items:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            identity = (
                str(item.get("kind") or ""),
                str(item.get("namespace") or ""),
                str(item.get("key") or ""),
            )
            locale = str(item.get("locale") or "ko").strip()
            cur.execute(
                """
                SELECT tu.id, tu.current_source_version_id, tr.id, tr.text
                FROM translation_units tu
                LEFT JOIN translations tr ON tr.unit_id = tu.id AND tr.locale = %s
                WHERE tu.kind = %s AND tu.namespace = %s AND tu.unit_key = %s
                  AND tu.current_source_version_id IS NOT NULL
                """,
                (locale, *identity),
            )
            row = cur.fetchone()
            if row is None:
                missing_unit += 1
                continue
            unit_id, source_version_id, translation_id, previous_text = row
            if translation_id is not None and previous_text == text:
                skipped_existing += 1
                continue

            change_id = uuid4()
            cur.execute(
                """
                INSERT INTO translation_changes(
                    id, group_id, unit_id, locale, source_version_id,
                    previous_text, proposed_text, status,
                    created_by, reviewed_by, reviewed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'approved', %s, %s, now())
                """,
                (
                    change_id,
                    group_id,
                    unit_id,
                    locale,
                    source_version_id,
                    previous_text,
                    text,
                    actor,
                    actor,
                ),
            )
            if translation_id is None:
                cur.execute(
                    """
                    INSERT INTO translations(
                        id, unit_id, locale, text, approved_source_version_id,
                        applied_change_id, approved_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (uuid4(), unit_id, locale, text, source_version_id, change_id, actor),
                )
            else:
                cur.execute(
                    """
                    UPDATE translations
                    SET text = %s,
                        approved_source_version_id = %s,
                        applied_change_id = %s,
                        approved_by = %s,
                        approved_at = now(),
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (text, source_version_id, change_id, actor, translation_id),
                )
            restored += 1
    return restored, skipped_existing, missing_unit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export approved translations before a DB reset or restore them afterward."
    )
    parser.add_argument("command", choices=("export", "import"))
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--actor", default=DEFAULT_ACTOR)
    args = parser.parse_args()
    database_url = _database_url(args.database_url)

    if args.command == "export":
        count = export_translations(database_url, args.file)
        print(f"exported approved translations: {count} -> {args.file}")
        return 0

    restored, skipped, missing = import_translations(database_url, args.file, args.actor)
    print(f"restored={restored} skipped_existing={skipped} missing_current_unit={missing}")
    if missing:
        print("Some backup identities do not exist in the current INT_STEAM source and were skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
