from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg


@dataclass(frozen=True, slots=True)
class BuildFileRecord:
    target: str
    relative_path: str
    operation: str
    sha256: str
    size: int

    def validate(self) -> None:
        if self.target not in {"addressables", "game_data"}:
            raise ValueError(f"unsupported build file target: {self.target}")
        if self.operation not in {"create", "replace"}:
            raise ValueError(f"unsupported build file operation: {self.operation}")
        if not self.relative_path or self.relative_path.startswith("/"):
            raise ValueError("build file relative_path must be non-empty and relative")
        if ".." in self.relative_path.replace("\\", "/").split("/"):
            raise ValueError("build file relative_path cannot traverse parents")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("build file sha256 must be lowercase SHA-256 hex")
        if self.size <= 0:
            raise ValueError("build file size must be positive")


@dataclass(frozen=True, slots=True)
class BuildRecord:
    id: UUID
    revision_id: UUID
    translation_fingerprint: str


def begin_build(
    conn: psycopg.Connection,
    *,
    revision_id: UUID,
    route: str,
    translation_fingerprint: str,
    git_commit: str | None = None,
    github_run_id: str | None = None,
) -> BuildRecord:
    if len(translation_fingerprint) != 64:
        raise ValueError("translation_fingerprint must be SHA-256 hex")
    build_id = uuid4()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO builds(
                id, revision_id, route, channel, translation_fingerprint,
                git_commit, github_run_id, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'building')
            """,
            (
                build_id,
                revision_id,
                route,
                "release",
                translation_fingerprint,
                git_commit,
                github_run_id,
            ),
        )
    return BuildRecord(build_id, revision_id, translation_fingerprint)


def record_build_files(
    conn: psycopg.Connection,
    build_id: UUID,
    files: tuple[BuildFileRecord, ...],
) -> None:
    identities: set[tuple[str, str]] = set()
    for item in files:
        item.validate()
        identity = (item.target, item.relative_path)
        if identity in identities:
            raise ValueError(f"duplicate build file identity: {identity}")
        identities.add(identity)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM build_files WHERE build_id = %s", (build_id,))
        if cur.fetchone()[0] != 0:
            raise RuntimeError(f"build files already recorded for immutable build {build_id}")
        cur.executemany(
            """
            INSERT INTO build_files(build_id, target, relative_path, operation, sha256, size)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    build_id,
                    item.target,
                    item.relative_path,
                    item.operation,
                    item.sha256,
                    item.size,
                )
                for item in files
            ],
        )


def set_build_status(conn: psycopg.Connection, build_id: UUID, status: str) -> None:
    if status not in {"building", "validated", "released", "failed"}:
        raise ValueError(f"unsupported build status: {status}")
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            UPDATE builds
            SET status = %s,
                released_at = CASE WHEN %s = 'released' THEN now() ELSE released_at END
            WHERE id = %s
            """,
            (status, status, build_id),
        )
        if cur.rowcount != 1:
            raise KeyError(f"build not found: {build_id}")
