from pathlib import Path

from pglast import parse_sql

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "database" / "migrations"


def test_database_uses_one_fresh_baseline_migration() -> None:
    assert [path.name for path in sorted(MIGRATIONS.glob("*.sql"))] == ["0001_initial.sql"]
    parse_sql((MIGRATIONS / "0001_initial.sql").read_text(encoding="utf-8"))


def test_schema_separates_source_history_pending_changes_and_production_translations() -> None:
    sql = (MIGRATIONS / "0001_initial.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE source_versions" in sql
    assert "CREATE TABLE source_changes" in sql
    assert "PRIMARY KEY (revision_id, unit_id)" in sql
    assert "status text NOT NULL DEFAULT 'applied' CHECK (status = 'applied')" in sql
    assert "CREATE TABLE translation_change_groups" in sql
    assert "CREATE TABLE translation_changes" in sql
    assert "CHECK (status IN ('pending', 'approved', 'rejected', 'superseded'))" in sql
    assert "CREATE TABLE translations" in sql
    translations_sql = sql.split("CREATE TABLE translations (", 1)[1].split(");", 1)[0]
    assert " status " not in translations_sql
    assert "applied_change_id" in translations_sql
    assert "CREATE TRIGGER translations_require_approved_change" in sql
    assert "CREATE VIEW translation_workbench" in sql
    assert "CREATE VIEW source_revision_change_summary" in sql
    assert "CREATE VIEW translation_change_group_summary" in sql
