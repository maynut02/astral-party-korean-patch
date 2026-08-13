from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg

_ALLOWED_STATUSES = {"draft", "reviewed", "approved"}


@dataclass(frozen=True, slots=True)
class TranslationWrite:
    unit_id: UUID
    locale: str
    text: str
    status: str
    source_fingerprint: str
    actor: str | None = None

    def validate(self) -> None:
        if self.status not in _ALLOWED_STATUSES:
            raise ValueError(f"unsupported translation status: {self.status}")
        if not self.locale:
            raise ValueError("locale cannot be empty")
        if len(self.source_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_fingerprint
        ):
            raise ValueError("source_fingerprint must be lowercase SHA-256 hex")


def upsert_translation(conn: psycopg.Connection, write: TranslationWrite) -> UUID:
    """Insert/update a translation and append immutable history in the same transaction."""
    write.validate()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, text, status
            FROM translations
            WHERE unit_id = %s AND locale = %s AND source_fingerprint = %s
            FOR UPDATE
            """,
            (write.unit_id, write.locale, write.source_fingerprint),
        )
        row = cur.fetchone()

        if row is None:
            translation_id = uuid4()
            cur.execute(
                """
                INSERT INTO translations(
                    id, unit_id, locale, text, status, source_fingerprint, updated_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    translation_id,
                    write.unit_id,
                    write.locale,
                    write.text,
                    write.status,
                    write.source_fingerprint,
                    write.actor,
                ),
            )
            old_text = None
            old_status = None
        else:
            translation_id = row[0]
            old_text = row[1]
            old_status = row[2]
            cur.execute(
                """
                UPDATE translations
                SET text = %s,
                    status = %s,
                    updated_at = now(),
                    updated_by = %s
                WHERE id = %s
                """,
                (
                    write.text,
                    write.status,
                    write.actor,
                    translation_id,
                ),
            )

        cur.execute(
            """
            INSERT INTO translation_history(
                id, translation_id, unit_id, locale,
                old_text, new_text, old_status, new_status,
                source_fingerprint, actor
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid4(),
                translation_id,
                write.unit_id,
                write.locale,
                old_text,
                write.text,
                old_status,
                write.status,
                write.source_fingerprint,
                write.actor,
            ),
        )
        return translation_id
