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
