from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

try:
    import psycopg
except ImportError as exc:
    raise SystemExit(
        "psycopg is required. Run: python -m pip install -r database/requirements.txt"
    ) from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
DATABASE_DIR = REPO_ROOT / "database"
if str(DATABASE_DIR) not in sys.path:
    sys.path.insert(0, str(DATABASE_DIR))

from local_env import (
    resolve_legacy_database_url,
    resolve_target_database_url,
)

DEFAULT_ACTOR = "legacy-control-site-import"


@dataclass(frozen=True)
class LegacyTranslation:
    kind: str
    namespace: str
    key: str
    text: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.kind, self.namespace, self.key)


def _normalize_text(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def load_legacy_translations(conn: psycopg.Connection) -> tuple[LegacyTranslation, ...]:
    items: list[LegacyTranslation] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, ko
            FROM lang_data
            WHERE is_deleted = false
              AND ko IS NOT NULL
              AND btrim(ko) <> ''
            ORDER BY name
            """
        )
        for name, ko in cur.fetchall():
            key = str(name or "").strip()
            text = _normalize_text(ko)
            if key and text:
                items.append(LegacyTranslation("lang", "lang", key, text))

        cur.execute(
            """
            SELECT category, \"key\", ko
            FROM astral_data
            WHERE is_deleted = false
              AND ko IS NOT NULL
              AND btrim(ko) <> ''
            ORDER BY category, \"key\"
            """
        )
        for category, key, ko in cur.fetchall():
            namespace = str(category or "").strip()
            unit_key = str(key or "").strip()
            text = _normalize_text(ko)
            if namespace and unit_key and text:
                items.append(LegacyTranslation("str", namespace, unit_key, text))

    identities = [item.identity for item in items]
    if len(identities) != len(set(identities)):
        raise RuntimeError("Legacy database contains duplicate translation identities")
    return tuple(items)


def import_legacy_translations(
    target_conn: psycopg.Connection,
    legacy_conn: psycopg.Connection,
    *,
    actor: str = DEFAULT_ACTOR,
    locale: str = "ko",
) -> dict[str, int]:
    legacy = load_legacy_translations(legacy_conn)
    counts = {
        "legacy": len(legacy),
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "missing_current_unit": 0,
    }

    with target_conn.transaction(), target_conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                tu.kind, tu.namespace, tu.unit_key,
                tu.id, tu.current_source_version_id, tr.id, tr.text
            FROM translation_units tu
            LEFT JOIN translations tr
                ON tr.unit_id = tu.id AND tr.locale = %s
            WHERE tu.current_source_version_id IS NOT NULL
            """,
            (locale,),
        )
        current = {
            (row[0], row[1], row[2]): (row[3], row[4], row[5], row[6])
            for row in cur.fetchall()
        }

        planned = []
        for item in legacy:
            row = current.get(item.identity)
            if row is None:
                counts["missing_current_unit"] += 1
                continue
            if row[2] is not None and row[3] == item.text:
                counts["unchanged"] += 1
                continue
            planned.append((item, row))

        if not planned:
            return counts

        group_id = uuid4()
        cur.execute(
            """
            INSERT INTO translation_change_groups(id, title, description, created_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                group_id,
                "Legacy control-site translation import",
                "Approved Korean values imported from astral-control-site lang_data/astral_data.",
                actor,
            ),
        )

        change_rows: list[tuple[object, ...]] = []
        inserts: list[tuple[object, ...]] = []
        updates: list[tuple[object, ...]] = []
        for item, (unit_id, source_version_id, translation_id, previous_text) in planned:
            change_id = uuid4()
            change_rows.append(
                (
                    change_id,
                    group_id,
                    unit_id,
                    locale,
                    source_version_id,
                    previous_text,
                    item.text,
                    actor,
                    actor,
                )
            )
            if translation_id is None:
                inserts.append(
                    (
                        uuid4(),
                        unit_id,
                        locale,
                        item.text,
                        source_version_id,
                        change_id,
                        actor,
                    )
                )
                counts["inserted"] += 1
            else:
                updates.append(
                    (item.text, source_version_id, change_id, actor, translation_id)
                )
                counts["updated"] += 1

        cur.executemany(
            """
            INSERT INTO translation_changes(
                id, group_id, unit_id, locale, source_version_id,
                previous_text, proposed_text, status,
                created_by, reviewed_by, reviewed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'approved', %s, %s, now())
            """,
            change_rows,
        )
        if inserts:
            cur.executemany(
                """
                INSERT INTO translations(
                    id, unit_id, locale, text, approved_source_version_id,
                    applied_change_id, approved_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                inserts,
            )
        if updates:
            cur.executemany(
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
                updates,
            )

    return counts

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import approved Korean translations from the previous astral-control-site DB."
    )
    parser.add_argument("--database-url", default="")
    parser.add_argument("--legacy-database-url", default="")
    parser.add_argument("--legacy-env", default="")
    parser.add_argument("--actor", default=DEFAULT_ACTOR)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target_url = resolve_target_database_url(direct=False, explicit=args.database_url)
    legacy_url, legacy_source = resolve_legacy_database_url(
        explicit=args.legacy_database_url,
        legacy_env=args.legacy_env or None,
    )
    if legacy_source is not None:
        print(f"Legacy DB configuration: {legacy_source}")

    with psycopg.connect(target_url) as target_conn, psycopg.connect(legacy_url) as legacy_conn:
        counts = import_legacy_translations(target_conn, legacy_conn, actor=args.actor)

    print(
        "legacy={legacy} inserted={inserted} updated={updated} unchanged={unchanged} "
        "missing_current_unit={missing_current_unit}".format(**counts)
    )
    if counts["missing_current_unit"]:
        print("Some legacy identities are not active in the current INT_STEAM source and were skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
