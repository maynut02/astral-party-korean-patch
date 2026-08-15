from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_local_env_parser_reads_plain_and_quoted_values(tmp_path: Path) -> None:
    module = _load("astral_local_env_test", ROOT / "database/local_env.py")
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "NEON_DATABASE_URL=postgresql://user:pass@example/db?sslmode=require\n"
        'NEON_DATABASE_URL_DIRECT="postgresql://direct/db"\n'
        "export EXTRA='value with spaces'\n",
        encoding="utf-8",
    )

    values = module._read_env_file(env)

    assert values["NEON_DATABASE_URL"].startswith("postgresql://")
    assert values["NEON_DATABASE_URL_DIRECT"] == "postgresql://direct/db"
    assert values["EXTRA"] == "value with spaces"


def test_legacy_translation_identity_and_text_normalization() -> None:
    module = _load(
        "astral_legacy_import_test",
        ROOT / "tools/database/import_legacy_translations.py",
    )
    item = module.LegacyTranslation("str", "STRCard", "421", "카드")

    assert item.identity == ("str", "STRCard", "421")
    assert module._normalize_text("  첫 줄\r\n둘째 줄  ") == "첫 줄\n둘째 줄"
