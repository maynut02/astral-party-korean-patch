from pathlib import Path

import pytest

from astral_builder.automation.sync import load_route_sync_config

ROOT = Path(__file__).resolve().parents[2]


def test_loads_int_steam_sync_config() -> None:
    config = load_route_sync_config(ROOT / "routes/int_steam.yaml")
    assert config.route == "INT_STEAM"
    assert config.lang_assets["en"] == "English"
    assert config.str_catalog_key == "GameData_INT"
    assert config.str_asset_prefix == "STR"
    assert config.tmp_catalog_key == "Afacad-Regular_TMP"


def test_route_sync_config_requires_all_languages(tmp_path: Path) -> None:
    path = tmp_path / "route.yaml"
    path.write_text(
        """
route: INT_STEAM
translation:
  lang:
    assets: {en: English}
  str:
    catalogKey: GameData_INT
    assetPrefix: STR
fonts:
  tmp:
    catalogKey: Afacad-Regular_TMP
    asset: Afacad-Regular_TMP
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cn_s/en/jp/cn_t"):
        load_route_sync_config(path)


def test_sync_output_contract(tmp_path: Path) -> None:
    from astral_builder.automation.sync import (
        PreparedRevision,
        SyncRevisionResult,
        write_sync_github_output,
    )
    from astral_builder.game.source import DownloadedCatalog, GameSource

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
