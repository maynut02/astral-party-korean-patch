from astral_builder.addressables.resolver import BundleOrigin, ResolvedBundle
from astral_builder.automation.build import _download_url, _release_name


def _bundle() -> ResolvedBundle:
    return ResolvedBundle(
        origin=BundleOrigin.REMOTE,
        primary_key="English",
        internal_id="remote.bundle",
        bundle_name="root-id",
        cache_hash="a" * 32,
        expected_size=10,
        download_url="https://example.test/a.bundle",
        cache_relative_path=f"root-id/{'a' * 32}/__data",
    )


def test_release_asset_name_is_flat_and_deterministic() -> None:
    name = _release_name("lang", _bundle())
    assert name == f"lang-root-id-{'a' * 32}.bin"
    assert "/" not in name


def test_download_url_joins_release_asset_base() -> None:
    assert _download_url("https://example.test/release/", "asset.bin") == (
        "https://example.test/release/asset.bin"
    )
