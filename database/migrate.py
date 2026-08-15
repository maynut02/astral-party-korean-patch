#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

try:
    import psycopg
except ImportError as exc:
    raise SystemExit("psycopg is required. Install the builder package first.") from exc

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def migration_files() -> tuple[Path, ...]:
    return tuple(sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_database_url(explicit: str = "") -> str:
    if explicit.strip():
        return explicit.strip()
    for name in ("DATABASE_URL_DIRECT", "NEON_DATABASE_URL_DIRECT", "DATABASE_URL"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise SystemExit("DATABASE_URL_DIRECT or NEON_DATABASE_URL_DIRECT is required")


def migrate(database_url: str) -> int:
    files = migration_files()
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    sha256 text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()

        applied = 0
        for path in files:
            version = path.name
            sql_text = path.read_text(encoding="utf-8")
            digest = sha256_text(sql_text)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sha256 FROM schema_migrations WHERE version = %s",
                    (version,),
                )
                row = cur.fetchone()
            if row is not None:
                if row[0] != digest:
                    raise RuntimeError(
                        f"applied migration changed on disk: {version} "
                        f"database={row[0]} file={digest}"
                    )
                continue

            try:
                with conn.transaction(), conn.cursor() as cur:
                    cur.execute(sql_text)
                    cur.execute(
                        "INSERT INTO schema_migrations(version, sha256) VALUES (%s, %s)",
                        (version, digest),
                    )
            except Exception:
                conn.rollback()
                raise
            applied += 1
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply Astral Party PostgreSQL migrations"
    )
    parser.add_argument("--database-url", default="")
    args = parser.parse_args()
    count = migrate(resolve_database_url(args.database_url))
    print(f"applied migrations: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
