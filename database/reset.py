from __future__ import annotations

import argparse
import os

import psycopg

CONFIRMATION = "RESET_ASTRAL_DATABASE"

DROP_STATEMENTS = (
    "DROP VIEW IF EXISTS translation_change_group_summary CASCADE",
    "DROP VIEW IF EXISTS source_revision_change_summary CASCADE",
    "DROP VIEW IF EXISTS translation_workbench CASCADE",
    "DROP VIEW IF EXISTS current_source_texts CASCADE",
    "DROP TABLE IF EXISTS build_files CASCADE",
    "DROP TABLE IF EXISTS builds CASCADE",
    "DROP TABLE IF EXISTS translations CASCADE",
    "DROP TABLE IF EXISTS translation_changes CASCADE",
    "DROP TABLE IF EXISTS translation_change_groups CASCADE",
    "DROP TABLE IF EXISTS translation_history CASCADE",
    "DROP TABLE IF EXISTS source_changes CASCADE",
    "DROP TABLE IF EXISTS source_texts CASCADE",
    "DROP TABLE IF EXISTS source_versions CASCADE",
    "DROP TABLE IF EXISTS translation_units CASCADE",
    "DROP TABLE IF EXISTS asset_locations CASCADE",
    "DROP TABLE IF EXISTS game_revisions CASCADE",
    "DROP TABLE IF EXISTS schema_migrations CASCADE",
    "DROP FUNCTION IF EXISTS enforce_approved_translation_change() CASCADE",
    "DROP FUNCTION IF EXISTS enforce_translation_change_source() CASCADE",
)


def reset(database_url: str) -> None:
    with (
        psycopg.connect(database_url) as conn,
        conn.transaction(),
        conn.cursor() as cur,
    ):
        for statement in DROP_STATEMENTS:
            cur.execute(statement)


def main() -> int:
    parser = argparse.ArgumentParser(description="Drop all Astral Party project tables/views.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL_DIRECT", ""),
        help="PostgreSQL connection string; defaults to DATABASE_URL_DIRECT",
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        parser.error(f"--confirm must be exactly {CONFIRMATION}")
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL_DIRECT is required")
    reset(args.database_url)
    print("Astral Party database objects removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
