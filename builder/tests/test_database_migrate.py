from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "database/migrate.py"
SPEC = importlib.util.spec_from_file_location("database_migrate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_database_url_prefers_explicit_then_direct_environment(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://pooled")
    monkeypatch.setenv("NEON_DATABASE_URL_DIRECT", "postgresql://neon-direct")
    monkeypatch.setenv("DATABASE_URL_DIRECT", "postgresql://direct")

    assert MODULE.resolve_database_url("postgresql://explicit") == "postgresql://explicit"
    assert MODULE.resolve_database_url() == "postgresql://direct"


def test_database_url_requires_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(MODULE, "PROJECT_ENV", tmp_path / ".env")
    for name in ("DATABASE_URL", "NEON_DATABASE_URL_DIRECT", "DATABASE_URL_DIRECT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL_DIRECT"):
        MODULE.resolve_database_url()


def test_database_url_loads_repository_env(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "NEON_DATABASE_URL_DIRECT=postgresql://from-project-env\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "PROJECT_ENV", env_path)
    for name in ("DATABASE_URL", "NEON_DATABASE_URL_DIRECT", "DATABASE_URL_DIRECT"):
        monkeypatch.delenv(name, raising=False)

    assert MODULE.resolve_database_url() == "postgresql://from-project-env"
