from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg


@dataclass(frozen=True, slots=True)
class TranslationChangeGroupWrite:
    title: str
    description: str | None = None
    actor: str | None = None

    def validate(self) -> None:
        if not self.title.strip():
            raise ValueError("translation change group title cannot be empty")


@dataclass(frozen=True, slots=True)
class TranslationProposal:
    group_id: UUID
    unit_id: UUID
    locale: str
    source_version_id: UUID
    text: str
    actor: str | None = None

    def validate(self) -> None:
        if not self.locale.strip():
            raise ValueError("locale cannot be empty")
        if not self.text.strip():
            raise ValueError("translation proposal text cannot be empty")


def create_translation_change_group(
    conn: psycopg.Connection,
    write: TranslationChangeGroupWrite,
) -> UUID:
    write.validate()
    group_id = uuid4()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO translation_change_groups(id, title, description, created_by)
            VALUES (%s, %s, %s, %s)
            """,
            (group_id, write.title.strip(), write.description, write.actor),
        )
    return group_id


def propose_translation(conn: psycopg.Connection, proposal: TranslationProposal) -> UUID:
    proposal.validate()
    change_id = uuid4()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM translation_units
            WHERE id = %s AND current_source_version_id = %s
            """,
            (proposal.unit_id, proposal.source_version_id),
        )
        if cur.fetchone() is None:
            raise ValueError("proposal must target the unit current source version")
        cur.execute(
            """
            SELECT text
            FROM translations
            WHERE unit_id = %s AND locale = %s
            """,
            (proposal.unit_id, proposal.locale),
        )
        row = cur.fetchone()
        previous_text = None if row is None else row[0]
        cur.execute(
            """
            INSERT INTO translation_changes(
                id, group_id, unit_id, locale, source_version_id,
                previous_text, proposed_text, status, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
            """,
            (
                change_id,
                proposal.group_id,
                proposal.unit_id,
                proposal.locale,
                proposal.source_version_id,
                previous_text,
                proposal.text,
                proposal.actor,
            ),
        )
    return change_id


def approve_translation_change(
    conn: psycopg.Connection,
    change_id: UUID,
    *,
    actor: str | None = None,
) -> UUID:
    """Approve one proposal and atomically make it the production translation."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            SELECT unit_id, locale, source_version_id, proposed_text, status
            FROM translation_changes
            WHERE id = %s
            FOR UPDATE
            """,
            (change_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"translation change not found: {change_id}")
        unit_id, locale, source_version_id, proposed_text, status = row
        if status != "pending":
            raise ValueError(f"translation change is not pending: {status}")
        cur.execute(
            "SELECT current_source_version_id FROM translation_units WHERE id = %s",
            (unit_id,),
        )
        current_source = cur.fetchone()
        if current_source is None or current_source[0] != source_version_id:
            raise ValueError("translation proposal source is no longer current")

        # Once one proposal is approved, older competing pending proposals cannot later replace
        # it accidentally. They remain in the audit log as superseded.
        cur.execute(
            """
            UPDATE translation_changes
            SET status = 'superseded', reviewed_by = %s, reviewed_at = now()
            WHERE unit_id = %s AND locale = %s AND status = 'pending' AND id <> %s
            """,
            (actor, unit_id, locale, change_id),
        )
        cur.execute(
            """
            UPDATE translation_changes
            SET status = 'approved', reviewed_by = %s, reviewed_at = now()
            WHERE id = %s
            """,
            (actor, change_id),
        )

        cur.execute(
            "SELECT id FROM translations WHERE unit_id = %s AND locale = %s FOR UPDATE",
            (unit_id, locale),
        )
        existing = cur.fetchone()
        if existing is None:
            translation_id = uuid4()
            cur.execute(
                """
                INSERT INTO translations(
                    id, unit_id, locale, text, approved_source_version_id,
                    applied_change_id, approved_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    translation_id,
                    unit_id,
                    locale,
                    proposed_text,
                    source_version_id,
                    change_id,
                    actor,
                ),
            )
        else:
            translation_id = existing[0]
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
                (proposed_text, source_version_id, change_id, actor, translation_id),
            )
    return translation_id


def reject_translation_change(
    conn: psycopg.Connection,
    change_id: UUID,
    *,
    actor: str | None = None,
) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            UPDATE translation_changes
            SET status = 'rejected', reviewed_by = %s, reviewed_at = now()
            WHERE id = %s AND status = 'pending'
            """,
            (actor, change_id),
        )
        if cur.rowcount != 1:
            raise ValueError("translation change is missing or is not pending")
