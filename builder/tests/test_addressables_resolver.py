from test_addressables_catalog import _catalog_json

from astral_builder.addressables import AddressablesCatalog, BundleOrigin, resolve_target
from astral_builder.game.source import GameSource


def test_resolves_remote_bundle_to_download_and_cache_paths() -> None:
    catalog = AddressablesCatalog.from_json(_catalog_json())
    source = GameSource(
        route="INT_STEAM",
        version="3.2.0",
        revision="1042",
        source_url="https://cdn.example/content/1042",
        catalog_url="https://cdn.example/content/1042/catalog_3.2.0.json",
    )

    target = resolve_target(catalog, source, logical_name="lang.en", catalog_key="English")

    assert target.logical_name == "lang.en"
    assert len(target.bundles) == 1
    bundle = target.bundles[0]
    assert bundle.origin is BundleOrigin.REMOTE
    assert bundle.download_url == "https://cdn.example/content/1042/target.bundle"
    assert bundle.cache_relative_path == f"bundle-root/{'a' * 32}/__data"
    assert bundle.expected_size == 12345
