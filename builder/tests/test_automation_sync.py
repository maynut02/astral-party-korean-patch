from pathlib import Path

import pytest

from astral_builder.automation.sync import (
    PreparedRevision,
    _canonicalize_units_from_fallback,
    load_route_sync_config,
)
from astral_builder.formats.model import SourceStrings, TranslationKind, TranslationUnit
from astral_builder.game.source import DownloadedCatalog, GameSource

ROOT = Path(__file__).resolve().parents[2]


def test_loads_int_steam_sync_config() -> None:
    config = load_route_sync_config(ROOT / "routes/int_steam.yaml")
    assert config.route == "INT_STEAM"
    assert config.lang_assets["en"] == "English"
    assert config.str_catalog_key == "GameData_INT"
    assert config.str_asset_prefix == "STR"
    assert config.tmp_catalog_key == "Afacad-Regular_TMP"


def test_loads_int_android_sync_config() -> None:
    config = load_route_sync_config(ROOT / "routes/int_android.yaml")
    assert config.route == "INT_ANDROID"
    assert config.platform == "android"
    assert config.canonical_fallback_route == "INT_STEAM"
    assert config.lang_assets == {"jp": "Japanese"}
    assert config.lang_target == "jp"
    assert config.str_catalog_key == "GameData_INT"
    assert config.str_target_field == "jp"
    assert config.tmp_catalog_key == "MochiyPopOne-Regular_TMP"
    assert config.tmp_asset_name == "MochiyPopOne-Regular_TMP"
    assert config.legacy_font_name == "MochiyPopOne-Regular"


def test_route_sync_config_allows_route_specific_language_assets(tmp_path: Path) -> None:
    path = tmp_path / "route.yaml"
    path.write_text(
        """
route: CN_STEAM
translation:
  canonicalFallbackRoute: INT_STEAM
  lang:
    assets: {cn_s: Simplified Chinese}
    target: cn_s
  str:
    catalogKey: GameData_CN
    assetPrefix: STR
    targetField: cn_s
fonts:
  tmp:
    catalogKey: JingNanBoBoHei_TMP
    asset: JingNanBoBoHei_TMP
  legacy:
    asset: JingNanBoBoHei
resources:
  root: resources/cn_steam
""",
        encoding="utf-8",
    )
    config = load_route_sync_config(path)
    assert config.lang_assets == {"cn_s": "Simplified Chinese"}
    assert config.lang_target == "cn_s"
    assert config.str_target_field == "cn_s"
    assert config.canonical_fallback_route == "INT_STEAM"
    assert config.legacy_font_name == "JingNanBoBoHei"


def test_route_sync_config_rejects_unknown_language_code(tmp_path: Path) -> None:
    path = tmp_path / "route.yaml"
    path.write_text(
        """
route: TEST
translation:
  lang:
    assets: {ko: Korean}
    target: ko
  str:
    catalogKey: GameData
    assetPrefix: STR
fonts:
  tmp:
    catalogKey: Font_TMP
    asset: Font_TMP
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cn_s/en/jp/cn_t"):
        load_route_sync_config(path)


class _FallbackCursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, params):
        assert params == ("INT_STEAM", "3.2.0", "116")

    def fetchall(self):
        return self.rows


class _FallbackConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _FallbackCursor(self.rows)


def test_canonical_fallback_only_fills_missing_source_fields(tmp_path: Path) -> None:
    prepared = PreparedRevision(
        source=GameSource(
            route="CN_STEAM",
            version="3.2.0",
            revision="116",
            source_url="https://example.test/CN_STEAM/116",
            catalog_url="https://example.test/CN_STEAM/116/catalog.json",
        ),
        catalog_hash="f" * 32,
        catalog=DownloadedCatalog(tmp_path / "catalog.json", "a" * 64, 10),
        units=(
            TranslationUnit(
                kind=TranslationKind.STR,
                namespace="STRCard",
                key="1",
                source=SourceStrings(cn_s="中国值"),
            ),
        ),
        asset_locations=(),
        downloaded_bundles=(),
        empty_str_assets=(),
        canonical_fallback_route="INT_STEAM",
    )
    conn = _FallbackConnection([("str", "STRCard", "1", "国际简中", "English", "日本語", "繁體")])
    units = _canonicalize_units_from_fallback(conn, prepared)
    assert units[0].source == SourceStrings(cn_s="中国值", en="English", jp="日本語", cn_t="繁體")


def test_sync_output_contract(tmp_path: Path) -> None:
    from astral_builder.automation.sync import SyncRevisionResult, write_sync_github_output

    output = tmp_path / "gha-output"
    prepared = PreparedRevision(
        source=GameSource(
            route="INT_STEAM",
            version="3.2.0",
            revision="116",
            source_url="https://example.test/116",
            catalog_url="https://example.test/116/catalog_3.2.0.json",
        ),
        catalog_hash="f" * 32,
        catalog=DownloadedCatalog(tmp_path / "catalog.json", "a" * 64, 10),
        units=(),
        asset_locations=(),
        downloaded_bundles=(),
        empty_str_assets=(),
    )
    result = SyncRevisionResult("revision-uuid", False, 0, 0, 0, ())
    write_sync_github_output(result, prepared, output)
    text = output.read_text()
    assert "revision_id=revision-uuid" in text
    assert "revision=116" in text
    assert f"catalog_hash={'f' * 32}" in text
