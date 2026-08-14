from pathlib import Path

import pytest

from astral_builder.addressables.resolver import BundleOrigin, ResolvedBundle, ResolvedTarget
from astral_builder.automation.sync import (
    PreparedRevision,
    _bundle_locations,
    _canonicalize_units_from_fallback,
    load_route_sync_config,
)
from astral_builder.formats.model import SourceStrings, TranslationKind, TranslationUnit
from astral_builder.game.source import DownloadedCatalog, GameSource
from astral_builder.source.downloader import DownloadedBundle

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


def test_bundle_locations_disambiguate_multiple_remote_dependencies(tmp_path: Path) -> None:
    bundles = tuple(
        ResolvedBundle(
            origin=BundleOrigin.REMOTE,
            primary_key=f"font-dependency-{index}",
            internal_id=f"{{App.WebServerConfig.Path}}/font-{index}.bundle",
            bundle_name=f"font-bundle-{index}",
            cache_hash=str(index) * 32,
            expected_size=10 + index,
            download_url=f"https://example.test/font-{index}.bundle",
            cache_relative_path=f"font-bundle-{index}/{str(index) * 32}/__data",
        )
        for index in (1, 2)
    )
    target = ResolvedTarget(
        logical_name="tmp-font",
        catalog_key="MochiyPopOne-Regular_TMP",
        bundles=bundles,
    )
    downloaded = tuple(
        DownloadedBundle(
            resolved=bundle,
            path=tmp_path / f"{bundle.bundle_name}.bundle",
            sha256=("a" if index == 1 else "b") * 64,
            size=10 + index,
        )
        for index, bundle in enumerate(bundles, start=1)
    )

    locations = _bundle_locations(target, downloaded)

    assert len(locations) == 2
    assert len({location.identity for location in locations}) == 2
    assert [location.catalog_key for location in locations] == [
        "MochiyPopOne-Regular_TMP",
        "MochiyPopOne-Regular_TMP",
    ]
    assert [location.asset_name for location in locations] == [
        f"font-bundle-1/{'1' * 32}",
        f"font-bundle-2/{'2' * 32}",
    ]


def test_bundle_locations_keep_legacy_identity_for_single_remote_dependency(tmp_path: Path) -> None:
    bundle = ResolvedBundle(
        origin=BundleOrigin.REMOTE,
        primary_key="English",
        internal_id="{App.WebServerConfig.Path}/english.bundle",
        bundle_name="english-bundle",
        cache_hash="a" * 32,
        expected_size=4,
        download_url="https://example.test/english.bundle",
        cache_relative_path=f"english-bundle/{'a' * 32}/__data",
    )
    target = ResolvedTarget("lang:en", "English", (bundle,))
    downloaded = (DownloadedBundle(bundle, tmp_path / "english.bundle", "b" * 64, 4),)

    locations = _bundle_locations(target, downloaded)

    assert len(locations) == 1
    assert locations[0].asset_name == "English"


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
    def __init__(self, revision_row, source_rows):
        self.revision_row = revision_row
        self.source_rows = source_rows
        self.query_index = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.query_index += 1
        if self.query_index == 1:
            assert "ORDER BY detected_at DESC, processed_at DESC" in sql
            assert params == ("INT_STEAM", "3.2.0")
        elif self.query_index == 2:
            assert "WHERE st.revision_id = %s" in sql
            assert params == ("fallback-revision-id",)
        else:
            raise AssertionError("unexpected fallback query")

    def fetchone(self):
        assert self.query_index == 1
        return self.revision_row

    def fetchall(self):
        assert self.query_index == 2
        return self.source_rows


class _FallbackConnection:
    def __init__(self, revision_row, source_rows):
        self.revision_row = revision_row
        self.source_rows = source_rows

    def cursor(self):
        return _FallbackCursor(self.revision_row, self.source_rows)


def _prepared_fallback_revision(tmp_path: Path, *, route: str, revision: str) -> PreparedRevision:
    return PreparedRevision(
        source=GameSource(
            route=route,
            version="3.2.0",
            revision=revision,
            source_url=f"https://example.test/{route}/{revision}",
            catalog_url=f"https://example.test/{route}/{revision}/catalog.json",
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


def test_canonical_fallback_uses_latest_processed_revision_even_when_revision_differs(
    tmp_path: Path,
) -> None:
    prepared = _prepared_fallback_revision(tmp_path, route="INT_ANDROID", revision="117")
    conn = _FallbackConnection(
        ("fallback-revision-id", "116"),
        [("str", "STRCard", "1", "国际简中", "English", "日本語", "繁體")],
    )

    units = _canonicalize_units_from_fallback(conn, prepared)

    assert units[0].source == SourceStrings(cn_s="中国值", en="English", jp="日本語", cn_t="繁體")


def test_canonical_fallback_rejects_missing_processed_version(tmp_path: Path) -> None:
    prepared = _prepared_fallback_revision(tmp_path, route="CN_STEAM", revision="118")
    conn = _FallbackConnection(None, [])

    with pytest.raises(
        RuntimeError, match="canonical fallback has no processed source revision: INT_STEAM/3.2.0"
    ):
        _canonicalize_units_from_fallback(conn, prepared)


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
