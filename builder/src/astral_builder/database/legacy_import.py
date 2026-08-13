from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg

from astral_builder.formats.model import SourceStrings, normalize_text

Identity = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class LegacyTranslation:
    kind: str
    namespace: str
    unit_key: str
    text: str
    source_fingerprint: str

    @property
    def identity(self) -> Identity:
        return (self.kind, self.namespace, self.unit_key)


@dataclass(frozen=True, slots=True)
class LegacyImportResult:
    revision_id: UUID
    legacy_translation_count: int
    imported_count: int
    existing_count: int
    missing_unit_count: int
    source_matching_count: int
    source_changed_count: int


def _clean_translation(value: object) -> str:
    return normalize_text(str(value or "")).strip()


def _source_fingerprint(cn_s: object, en: object, jp: object, cn_t: object) -> str:
    return SourceStrings(
        cn_s=str(cn_s or ""),
        en=str(en or ""),
        jp=str(jp or ""),
        cn_t=str(cn_t or ""),
    ).fingerprint


def legacy_lang_translation(row: tuple[object, ...]) -> LegacyTranslation | None:
    name = str(row[0] or "").strip()
    text = _clean_translation(row[5])
    if not name or not text:
        return None
    return LegacyTranslation(
        kind="lang",
        namespace="lang",
        unit_key=name,
        text=text,
        source_fingerprint=_source_fingerprint(row[1], row[2], row[3], row[4]),
    )


def legacy_str_translation(row: tuple[object, ...]) -> LegacyTranslation | None:
    namespace = str(row[0] or "").strip()
    unit_key = str(row[1] or "").strip()
    text = _clean_translation(row[6])
    if not namespace or not unit_key or not text:
        return None
    return LegacyTranslation(
        kind="str",
        namespace=namespace,
        unit_key=unit_key,
        text=text,
        source_fingerprint=_source_fingerprint(row[2], row[3], row[4], row[5]),
    )


def load_legacy_translations(conn: psycopg.Connection) -> tuple[LegacyTranslation, ...]:
    translations: list[LegacyTranslation] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, cn_s, en, jp, cn_t, ko
            FROM lang_data
            WHERE is_deleted = false
              AND ko IS NOT NULL
              AND btrim(ko) <> ''
            ORDER BY name
            """
        )
        translations.extend(
            item for row in cur.fetchall() if (item := legacy_lang_translation(row)) is not None
        )

        cur.execute(
            """
            SELECT category, "key", cn_s, en, jp, cn_t, ko
            FROM astral_data
            WHERE is_deleted = false
              AND ko IS NOT NULL
              AND btrim(ko) <> ''
            ORDER BY category, "key"
            """
        )
        translations.extend(
            item for row in cur.fetchall() if (item := legacy_str_translation(row)) is not None
        )

    identities = [item.identity for item in translations]
    if len(identities) != len(set(identities)):
        raise ValueError("legacy database contains duplicate translation identities")
    return tuple(translations)


def resolve_target_revision(
    conn: psycopg.Connection,
    *,
    route: str,
    revision_id: UUID | None = None,
) -> UUID:
    with conn.cursor() as cur:
        if revision_id is not None:
            cur.execute(
                """
                SELECT id
                FROM game_revisions
                WHERE id = %s AND route = %s AND processed_at IS NOT NULL
                """,
                (revision_id, route),
            )
        else:
            cur.execute(
                """
                SELECT id
                FROM game_revisions
                WHERE route = %s AND processed_at IS NOT NULL
                ORDER BY detected_at DESC, id DESC
                LIMIT 1
                """,
                (route,),
            )
        row = cur.fetchone()
    if row is None:
        if revision_id is None:
            raise KeyError(f"processed game revision not found for route: {route}")
        raise KeyError(f"processed game revision not found: {revision_id}")
    return row[0]


def import_legacy_translations(
    target_conn: psycopg.Connection,
    legacy_conn: psycopg.Connection,
    *,
    route: str = "INT_STEAM",
    revision_id: UUID | None = None,
    locale: str = "ko",
    actor: str = "legacy-import",
) -> LegacyImportResult:
    """Import existing Korean text once without overwriting translations in the new database."""
    resolved_revision_id = resolve_target_revision(
        target_conn,
        route=route,
        revision_id=revision_id,
    )
    legacy = load_legacy_translations(legacy_conn)
    if not legacy:
        raise ValueError("legacy database contains no active Korean translations")

    with target_conn.transaction(), target_conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                tu.kind, tu.namespace, tu.unit_key, tu.id, st.source_fingerprint
            FROM source_texts st
            JOIN translation_units tu ON tu.id = st.unit_id
            WHERE st.revision_id = %s
            """,
            (resolved_revision_id,),
        )
        current_units: dict[Identity, tuple[UUID, str]] = {
            (row[0], row[1], row[2]): (row[3], row[4]) for row in cur.fetchall()
        }
        if not current_units:
            raise ValueError(
                f"target revision has no translation units: {resolved_revision_id}"
            )

        cur.execute(
            """
            SELECT tr.unit_id
            FROM translations tr
            JOIN source_texts st ON st.unit_id = tr.unit_id
            WHERE tr.locale = %s AND st.revision_id = %s
            """,
            (locale, resolved_revision_id),
        )
        existing_unit_ids = {row[0] for row in cur.fetchall()}

        translation_rows: list[tuple[object, ...]] = []
        history_rows: list[tuple[object, ...]] = []
        existing_count = 0
        missing_unit_count = 0
        source_matching_count = 0
        source_changed_count = 0

        for item in legacy:
            current = current_units.get(item.identity)
            if current is None:
                missing_unit_count += 1
                continue
            unit_id, current_source_fingerprint = current
            if unit_id in existing_unit_ids:
                existing_count += 1
                continue

            translation_id = uuid4()
            translation_rows.append(
                (
                    translation_id,
                    unit_id,
                    locale,
                    item.text,
                    "draft",
                    item.source_fingerprint,
                    actor,
                )
            )
            history_rows.append(
                (
                    uuid4(),
                    translation_id,
                    unit_id,
                    locale,
                    item.text,
                    "draft",
                    item.source_fingerprint,
                    actor,
                )
            )
            if item.source_fingerprint == current_source_fingerprint:
                source_matching_count += 1
            else:
                source_changed_count += 1

        if translation_rows:
            cur.executemany(
                """
                INSERT INTO translations(
                    id, unit_id, locale, text, status, source_fingerprint, updated_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (unit_id, locale) DO NOTHING
                """,
                translation_rows,
            )
            cur.executemany(
                """
                INSERT INTO translation_history(
                    id, translation_id, unit_id, locale,
                    old_text, new_text, old_status, new_status,
                    source_fingerprint, actor
                )
                VALUES (%s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s)
                """,
                history_rows,
            )

    return LegacyImportResult(
        revision_id=resolved_revision_id,
        legacy_translation_count=len(legacy),
        imported_count=len(translation_rows),
        existing_count=existing_count,
        missing_unit_count=missing_unit_count,
        source_matching_count=source_matching_count,
        source_changed_count=source_changed_count,
    )


def write_github_output(result: LegacyImportResult, path: str) -> None:
    values = {
        "revision_id": str(result.revision_id),
        "legacy_translation_count": result.legacy_translation_count,
        "imported_count": result.imported_count,
        "existing_count": result.existing_count,
        "missing_unit_count": result.missing_unit_count,
        "source_matching_count": result.source_matching_count,
        "source_changed_count": result.source_changed_count,
    }
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(
        description="One-shot import of legacy Korean translations into the rebuilt database."
    )
    parser.add_argument("--revision-id")
    parser.add_argument("--route", default="INT_STEAM")
    parser.add_argument("--github-output")
    args = parser.parse_args(argv)

    target_url = os.environ.get("DATABASE_URL", "").strip()
    legacy_url = os.environ.get("LEGACY_DATABASE_URL", "").strip()
    if not target_url:
        raise SystemExit("DATABASE_URL is required")
    if not legacy_url:
        raise SystemExit("LEGACY_DATABASE_URL is required")

    requested_revision = UUID(args.revision_id) if args.revision_id else None
    with psycopg.connect(target_url) as target_conn, psycopg.connect(legacy_url) as legacy_conn:
        result = import_legacy_translations(
            target_conn,
            legacy_conn,
            route=args.route,
            revision_id=requested_revision,
        )

    if args.github_output:
        write_github_output(result, args.github_output)
    print(
        json.dumps(
            {
                "revisionId": str(result.revision_id),
                "legacyTranslationCount": result.legacy_translation_count,
                "importedCount": result.imported_count,
                "existingCount": result.existing_count,
                "missingUnitCount": result.missing_unit_count,
                "sourceMatchingCount": result.source_matching_count,
                "sourceChangedCount": result.source_changed_count,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
