from pathlib import Path

from pglast import parse_sql

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "database" / "migrations"


def test_database_migrations_are_complete_and_parseable() -> None:
    migrations = sorted(MIGRATIONS.glob("*.sql"))
    assert [path.name for path in migrations] == [
        "0001_initial.sql",
        "0002_user_profile_details.sql",
        "0003_user_game_profile_constraints.sql",
        "0004_translation_changes_creator_status_index.sql",
        "0005_patch_watcher.sql",
    ]

    for migration in migrations:
        parse_sql(migration.read_text(encoding="utf-8"))


def test_patch_watcher_schema_keeps_runtime_version_and_route_state() -> None:
    sql = (MIGRATIONS / "0005_patch_watcher.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE patch_watch_config" in sql
    assert "game_version text NOT NULL" in sql
    assert "CREATE TABLE patch_watch_routes" in sql
    assert "last_dispatched_catalog_hash" in sql


def test_schema_separates_source_history_pending_changes_and_production_translations() -> None:
    sql = (MIGRATIONS / "0001_initial.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE app_users" in sql
    assert "VALUES ('bot', 'bot@system.local', 'Bot', 'bot')" in sql
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
    assert "CREATE TABLE editor_dictionaries" in sql
    assert "CREATE TABLE editor_dictionary_entries" in sql
    assert "CREATE TABLE request_rate_limit_counters" in sql
    assert "CREATE VIEW translation_workbench" in sql
    assert "tc.status = 'pending'" in sql
    assert "tc.source_version_id = src.source_version_id" in sql
    assert "CREATE VIEW source_revision_change_summary" in sql
    assert "CREATE VIEW translation_change_group_summary" in sql
