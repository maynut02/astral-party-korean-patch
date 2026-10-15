"""Keep builder discovery and the dependency-free watcher on the same URL grammar."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from tools import patch_watcher

from astral_builder.game.source import GameSourceClient

ROUTES = ("INT_STEAM", "CN_STEAM", "INT_ANDROID", "CN_ANDROID", "INT_IOS")
VERSION = "3.3.0"
CATALOG_HASH = "a" * 32


@pytest.fixture(params=("builder", "watcher"))
def discover(request, monkeypatch):
    def run(
        source_url: str,
        route: str = "INT_STEAM",
        version: str = VERSION,
        *,
        allow_hash: bool = True,
    ):
        requested = []

        def fetch(url, *_args):
            requested.append(url)
            if "/api/hotaddressExtend/get?" in url:
                query = parse_qs(urlsplit(url).query)
                assert query == {"route": [route.upper()], "version": [version]}
                return json.dumps({"sourceUrl": source_url}).encode()
            assert allow_hash, "invalid sourceUrl must fail before the catalog hash request"
            return CATALOG_HASH.encode()

        if request.param == "builder":
            client = GameSourceClient(fetch=fetch)
            state = client.discover(route, version)
            assert client.fetch_catalog_hash(state) == CATALOG_HASH
        else:
            monkeypatch.setattr(patch_watcher, "_fetch", fetch)
            state = patch_watcher.discover_remote_state(route, version)
            assert state.catalog_hash == CATALOG_HASH
        return state, requested

    return run


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("prefix", ("", "/content/game"))
@pytest.mark.parametrize("suffix", ("", "/TmvW1l", "/999"))
@pytest.mark.parametrize("ending", ("", "/", "?token=abc/", "/?token=abc/#download"))
def test_discovery_keeps_revision_and_full_content_url(discover, route, prefix, suffix, ending):
    base = f"https://cdn.example{prefix}/{route}/{VERSION}/012{suffix}"
    state, requested = discover(base + ending, route.lower())
    query_and_fragment = ending.removeprefix("/")
    expected_source_url = base + query_and_fragment
    expected_hash_url = f"{base}/catalog_{VERSION}.hash{query_and_fragment}"

    assert state.route == route
    assert state.revision == "012"
    assert state.source_url == expected_source_url
    assert requested[-1] == expected_hash_url
    if hasattr(state, "catalog_url"):
        assert state.catalog_url == f"{base}/catalog_{VERSION}.json{query_and_fragment}"
        assert state.catalog_hash_url == expected_hash_url


@pytest.mark.parametrize("path", ("/1042", "/game/1042", "/012", "/game/012"))
@pytest.mark.parametrize("ending", ("", "/", "/?token=abc"))
def test_short_numeric_fixture_paths_remain_supported(discover, path, ending):
    state, _requested = discover(f"https://cdn.example{path}{ending}")
    assert state.revision == path.rsplit("/", 1)[-1]


@pytest.mark.parametrize(
    "path",
    (
        "",
        "/",
        "/INT_STEAM/3.3.0",
        "/INT_STEAM/3.3.0/",
        "/INT_STEAM/3.3.0/TmvW1l",
        "/INT_STEAM/3.3.0/TmvW1l/012",
        "/INT_STEAM/3.3.0//012",
        "/INT_STEAM/3.3.0/012//TmvW1l",
        "/INT_STEAM//012/TmvW1l",
        "//INT_STEAM/3.3.0/012",
        "/INT_STEAM/3.3.0/012//",
        "/CN_STEAM/3.3.0/012",
        "/CN_STEAM/3.3.0/012/TmvW1l",
        "/int_steam/3.3.0/012/TmvW1l",
        "/INT_UNKNOWN/3.3.0/012",
        "/INT_STEAM/3.2.0/012",
        "/INT_STEAM/3.2.0/012/999",
        "/INT_STEAM/3.3/012",
        "/INT_STEAM/012",
        "/3.3.0/012",
        "/INT_STEAM/3.3.0/-012/999",
        "/INT_STEAM/3.3.0/12.0/999",
        "/INT_STEAM/3.3.0/12a/999",
        "/INT_STEAM/3.3.0/１２/999",
        "/INT_STEAM/3.3.0/١٢/999",
        "/INT_STEAM/3.3.0/%30%31%32/999",
        "/INT_STEAM/3.3.0/012;extra/999",
        "/INT_STEAM/3.3.0/./012",
        "/INT_STEAM/3.3.0/../012",
        "/INT_STEAM/3.3.0/012/..",
        "/INT_STEAM/3.3.0/012/TmvW1l/999",
        "/game/TmvW1l",
        "/game/012/999",
        "/content/1042",
    ),
)
@pytest.mark.parametrize("ending", ("", "?revision=012&route=INT_STEAM&version=3.3.0"))
def test_malformed_paths_are_rejected_before_fetching_hash(discover, path, ending):
    with pytest.raises(RuntimeError, match="sourceUrl"):
        discover(f"https://cdn.example{path}{ending}", allow_hash=False)
