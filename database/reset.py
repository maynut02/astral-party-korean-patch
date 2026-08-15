from __future__ import annotations

import argparse

try:
    import psycopg
except ImportError as exc:
    raise SystemExit(
        "psycopg is required. Run: python -m pip install -r database/requirements.txt"
    ) from exc

from local_env import resolve_target_database_url

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
    with psycopg.connect(database_url) as conn:  # noqa: SIM117
        with conn.transaction(), conn.cursor() as cur:
            for statement in DROP_STATEMENTS:
                cur.execute(statement)


def main() -> int:
    parser = argparse.ArgumentParser(description="Drop all Astral Party project tables/views.")
    parser.add_argument(
        "--database-url",
        default="",
        help="Optional PostgreSQL connection string; otherwise repository .env is used",
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        parser.error(f"--confirm must be exactly {CONFIRMATION}")
    database_url = resolve_target_database_url(direct=True, explicit=args.database_url)
    reset(database_url)
    print("Astral Party database objects removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
