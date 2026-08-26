from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ENV = REPO_ROOT / ".env"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}

    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].lstrip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        values[key] = value

    return values


def load_project_env() -> None:
    """Load repository .env values without replacing existing process variables."""
    for key, value in read_env_file(PROJECT_ENV).items():
        os.environ.setdefault(key, value)


def resolve_database_url(*, direct: bool, explicit: str = "") -> str:
    load_project_env()

    if explicit.strip():
        return explicit.strip()

    names = (
        ("NEON_DATABASE_URL_DIRECT", "DATABASE_URL_DIRECT")
        if direct
        else ("NEON_DATABASE_URL", "DATABASE_URL")
    )

    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value

    joined = " / ".join(names)
    raise SystemExit(f"Database URL is missing. Set {joined} in {PROJECT_ENV}.")
