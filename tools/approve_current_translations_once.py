from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from uuid import UUID

import psycopg

ROOT = Path(__file__).resolve().parents[1]
BUILDER_SRC = ROOT / "builder" / "src"
if str(BUILDER_SRC) not in sys.path:
    sys.path.insert(0, str(BUILDER_SRC))

from astral_builder.database.repository import load_translation_snapshot
from astral_builder.database.translations import TranslationWrite, upsert_translation

ROUTES = ("INT_STEAM", "CN_STEAM", "INT_ANDROID")
ACTOR = "one-shot-approve-current-translations"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot helper that promotes non-empty Korean translations to approved "
            "for the current source fingerprint of the latest processed revision."
        )
    )
    parser.add_argument(
        "--route",
        choices=(*ROUTES, "ALL"),
        default="INT_ANDROID",
        help="route whose latest processed revision should be approved",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes; without this flag the script only prints a preview",
    )
    return parser.parse_args()


def latest_processed_revision(
    conn: psycopg.Connection, route: str
) -> tuple[UUID, str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, game_version, revision
            FROM game_revisions
            WHERE route = %s AND processed_at IS NOT NULL
            ORDER BY detected_at DESC, processed_at DESC, id DESC
            LIMIT 1
            """,
            (route,),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"no processed game revision found for route {route}")
    return UUID(str(row[0])), str(row[1]), str(row[2])


def approval_candidates(
    conn: psycopg.Connection, revision_id: UUID
) -> tuple[list[tuple[UUID, str, str, str, str]], list[str]]:
    """Return promotable rows and keys that still have no non-empty translation.

    A stale-fingerprint translation is intentionally eligible here because this is a
    one-shot operator action requested specifically to accept the currently imported
    translation corpus as-is. The selected text is copied to the current source
    fingerprint through the normal translation upsert/history path.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                st.unit_id,
                st.source_fingerprint,
                COALESCE(candidate.text, ''),
                COALESCE(candidate.status, ''),
                COALESCE(candidate.source_fingerprint, ''),
                tu.kind,
                tu.namespace,
                tu.unit_key
            FROM source_texts AS st
            JOIN translation_units AS tu ON tu.id = st.unit_id
            LEFT JOIN LATERAL (
                SELECT t.text, t.status, t.source_fingerprint
                FROM translations AS t
                WHERE t.unit_id = st.unit_id
                  AND t.locale = 'ko'
                  AND btrim(t.text) <> ''
                ORDER BY
                    (t.source_fingerprint = st.source_fingerprint) DESC,
                    t.updated_at DESC,
                    t.id DESC
                LIMIT 1
            ) AS candidate ON TRUE
            WHERE st.revision_id = %s
            ORDER BY tu.kind, tu.namespace, tu.unit_key
            """,
            (revision_id,),
        )
        rows = cur.fetchall()

    promotable: list[tuple[UUID, str, str, str, str]] = []
    untranslated: list[str] = []
    for row in rows:
        unit_id = UUID(str(row[0]))
        current_fingerprint = str(row[1])
        text = str(row[2])
        status = str(row[3])
        translation_fingerprint = str(row[4])
        identity = f"{row[5]}:{row[6]}:{row[7]}"
        if not text.strip():
            untranslated.append(identity)
            continue
        if status == "approved" and translation_fingerprint == current_fingerprint:
            continue
        promotable.append(
            (unit_id, current_fingerprint, text, status, translation_fingerprint)
        )
    return promotable, untranslated


def state_counts(conn: psycopg.Connection, revision_id: UUID) -> Counter[str]:
    snapshot = load_translation_snapshot(conn, revision_id)
    return Counter(unit.state.value for unit in snapshot.units)


def print_counts(prefix: str, counts: Counter[str]) -> None:
    order = ("approved", "reviewed", "draft", "needs_review", "untranslated")
    summary = ", ".join(f"{state}={counts.get(state, 0)}" for state in order)
    print(f"{prefix}: total={sum(counts.values())}, {summary}")


def process_route(
    conn: psycopg.Connection,
    route: str,
    *,
    apply: bool,
    seen: set[tuple[UUID, str]],
) -> tuple[int, int]:
    revision_id, game_version, revision = latest_processed_revision(conn, route)
    print(f"\n[{route}] game={game_version} revision={revision} id={revision_id}")
    before = state_counts(conn, revision_id)
    print_counts("before", before)

    candidates, untranslated = approval_candidates(conn, revision_id)
    unique_candidates = []
    for candidate in candidates:
        key = (candidate[0], candidate[1])
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)

    stale = sum(1 for _, current_fp, _, _, translation_fp in unique_candidates if translation_fp != current_fp)
    print(
        f"promotable={len(unique_candidates)} "
        f"(stale fingerprint copied={stale}), untranslated={len(untranslated)}"
    )

    if apply:
        for unit_id, current_fingerprint, text, _status, _translation_fingerprint in unique_candidates:
            upsert_translation(
                conn,
                TranslationWrite(
                    unit_id=unit_id,
                    locale="ko",
                    text=text,
                    status="approved",
                    source_fingerprint=current_fingerprint,
                    actor=ACTOR,
                ),
            )
        conn.commit()
        after = state_counts(conn, revision_id)
        print_counts("after ", after)
    else:
        print("dry-run only; no database changes were written")

    if untranslated:
        preview = ", ".join(untranslated[:10])
        suffix = " ..." if len(untranslated) > 10 else ""
        print(f"untranslated examples: {preview}{suffix}")
    return len(unique_candidates), len(untranslated)


def main() -> int:
    args = parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    routes = ROUTES if args.route == "ALL" else (args.route,)
    seen: set[tuple[UUID, str]] = set()
    total_promoted = 0
    total_untranslated = 0

    with psycopg.connect(database_url) as conn:
        for route in routes:
            promoted, untranslated = process_route(
                conn, route, apply=args.apply, seen=seen
            )
            total_promoted += promoted
            total_untranslated += untranslated

    action = "approved" if args.apply else "would approve"
    print(
        f"\nSummary: {action} {total_promoted} current translation entries; "
        f"untranslated entries={total_untranslated}."
    )
    if total_untranslated:
        print(
            "Some units have no non-empty Korean translation and therefore cannot become "
            "approved by status promotion alone."
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
