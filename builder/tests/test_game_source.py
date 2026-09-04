import hashlib
import json
from pathlib import Path

import pytest

from astral_builder.game.source import GameSourceClient, SourceDiscoveryError


def test_discovers_int_source_revision() -> None:
    def fetch(url: str, timeout: float) -> bytes:
        assert "selist.feimogames.com:7878" in url
        assert "route=INT_STEAM" in url
        assert "version=3.2.0" in url
        assert timeout == 12.5
        return json.dumps({"sourceUrl": "https://cdn.example/game/1042/"}).encode()

    client = GameSourceClient(fetch=fetch, timeout=12.5)
    source = client.discover("int_steam", "3.2.0")
    assert source.route == "INT_STEAM"
    assert source.revision == "1042"
    assert source.catalog_url == "https://cdn.example/game/1042/catalog_3.2.0.json"
    assert source.catalog_hash_url == "https://cdn.example/game/1042/catalog_3.2.0.hash"


def test_cn_android_uses_cn_hotaddress_host() -> None:
    client = GameSourceClient(fetch=lambda _url, _timeout: b"{}")
    url = client.hotaddress_url("CN_ANDROID", "3.2.0")
    assert "se-web-cn.feimogames.com:7878" in url
    assert "route=CN_ANDROID" in url
    assert "version=3.2.0" in url


def test_rejects_unknown_route() -> None:
    client = GameSourceClient(fetch=lambda _url, _timeout: b"{}")
    with pytest.raises(SourceDiscoveryError, match="unsupported route"):
        client.discover("UNKNOWN", "3.2.0")


def test_download_catalog_is_atomic_and_hashed(tmp_path: Path) -> None:
    catalog = json.dumps({"m_InternalIds": []}, separators=(",", ":")).encode()

    def fetch(url: str, _timeout: float) -> bytes:
        if "hotaddress" in url:
            return json.dumps({"sourceUrl": "https://cdn.example/1042"}).encode()
        return catalog

    client = GameSourceClient(fetch=fetch)
    source = client.discover("INT_STEAM", "3.2.0")
    target = tmp_path / "catalog.json"
    result = client.download_catalog(source, target)

    assert target.read_bytes() == catalog
    assert result.path == target
    assert result.size == len(catalog)
    assert result.sha256 == hashlib.sha256(catalog).hexdigest()


def test_download_rejects_non_catalog_json(tmp_path: Path) -> None:
    responses = iter(
        [
            json.dumps({"sourceUrl": "https://cdn.example/1042"}).encode(),
            b"{}",
        ]
    )
    client = GameSourceClient(fetch=lambda _url, _timeout: next(responses))
    source = client.discover("INT_STEAM", "3.2.0")
    with pytest.raises(SourceDiscoveryError, match="not an Addressables catalog"):
        client.download_catalog(source, tmp_path / "catalog.json")


def test_fetches_catalog_hash() -> None:
    responses = iter(
        [
            json.dumps({"sourceUrl": "https://cdn.example/1042"}).encode(),
            b"fd58ba01bbca5e5e389b5b73240df134\n",
        ]
    )
    client = GameSourceClient(fetch=lambda _url, _timeout: next(responses))
    source = client.discover("INT_STEAM", "3.2.0")
    assert client.fetch_catalog_hash(source) == "fd58ba01bbca5e5e389b5b73240df134"


def test_rejects_invalid_catalog_hash() -> None:
    responses = iter(
        [
            json.dumps({"sourceUrl": "https://cdn.example/1042"}).encode(),
            b"not-a-hash",
        ]
    )
    client = GameSourceClient(fetch=lambda _url, _timeout: next(responses))
    source = client.discover("INT_STEAM", "3.2.0")
    with pytest.raises(SourceDiscoveryError, match="32-character"):
        client.fetch_catalog_hash(source)
