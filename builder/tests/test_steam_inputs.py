from __future__ import annotations

import base64
import hashlib
import json
import struct
from contextlib import contextmanager
from dataclasses import asdict, astuple, replace
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from astral_builder.automation.build import StoredRevision
from astral_builder.automation.steam_inputs import SteamInputPlan, plan_steam_inputs
from astral_builder.game.source import GameSource, GameSourceClient
from astral_builder.source.runtime import RuntimeBundleError

ROOT = Path(__file__).resolve().parents[2]
CATALOG_HASH = "c" * 32
BUNDLE_RESOURCE = "UnityEngine.ResourceManagement.ResourceProviders.IAssetBundleResource"
ROUTES = {
    "INT_STEAM": ("8vJXnINT/AstralParty_INT_Data", "Afacad-Regular_TMP"),
    "CN_STEAM": ("8vJXn6CN/AstralParty_CN_Data", "JingNanBoBoHei_TMP"),
}
FONT_PATH = "StandaloneWindows64/current-font.bundle"
SCRIPT_PATH = "StandaloneWindows64/current-monoscripts.bundle"


def _serialized_string(value: str) -> bytes:
    raw = value.encode("ascii")
    return b"\x00" + struct.pack("<i", len(raw)) + raw


def _bundle_options(name: str) -> bytes:
    assembly = b"Unity.ResourceManager"
    class_name = b"UnityEngine.ResourceManagement.ResourceProviders.AssetBundleRequestOptions"
    payload = json.dumps(
        {
            "m_BundleName": name,
            "m_Hash": hashlib.md5(name.encode(), usedforsecurity=False).hexdigest(),
            "m_BundleSize": 123,
            "m_Crc": 0,
        }
    ).encode("utf-16-le")
    return (
        b"\x07"
        + bytes([len(assembly)])
        + assembly
        + bytes([len(class_name)])
        + class_name
        + struct.pack("<i", len(payload))
        + payload
    )


def _catalog(
    route: str,
    *,
    font_origin: str = "runtime",
    script_origin: str = "runtime",
    font_path: str = FONT_PATH,
    cycle: bool = False,
    missing_key: bool = False,
    missing_dependencies: bool = False,
) -> bytes:
    """Encode an actual Addressables catalog, including a recursive script dependency.

    Two distinct font locations share a path and a bucket repeats one location.
    An unrelated runtime bundle must never make it into the Steam filelist.
    """
    key = "missing-font" if missing_key else ROUTES[route][1]
    keys = ["font.bundle", "scripts.bundle", "tmp-dependencies", key, "unrelated.bundle"]
    key_data = bytearray(struct.pack("<i", len(keys)))
    offsets = []
    for value in keys:
        offsets.append(len(key_data))
        key_data.extend(_serialized_string(value))
    buckets = bytearray(struct.pack("<i", len(keys)))
    dependency_indices = [] if missing_dependencies else [0, 4, 0]
    for offset, indices in zip(offsets, [[0], [1], dependency_indices, [2], [3]], strict=True):
        buckets.extend(struct.pack("<ii", offset, len(indices)))
        for index in indices:
            buckets.extend(struct.pack("<i", index))

    font_options = _bundle_options("original-font-root")
    script_options = _bundle_options("original-script-root")
    unused_options = _bundle_options("unrelated-root")
    extra_data = font_options + script_options + unused_options

    def internal_id(origin: str, path: str) -> str:
        prefix = (
            "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
            if origin == "runtime"
            else "{App.WebServerConfig.Path}"
        )
        return f"{prefix}/{path}"

    # internal id, provider, dependency key, dependency hash, options, primary key, type
    rows = [
        (0, 0, 1, 0, 0, 0, 0),
        (1, 0, 2 if cycle else -1, 0, len(font_options), 1, 0),
        (2, 1, 2, 0, -1, 3, 1),
        (3, 0, -1, 0, len(font_options) + len(script_options), 4, 0),
        (0, 0, 1, 0, 0, 0, 0),
    ]
    entries = struct.pack("<i", len(rows)) + b"".join(struct.pack("<7i", *row) for row in rows)
    return json.dumps(
        {
            "m_LocatorId": "steam-fixture",
            "m_BuildResultHash": CATALOG_HASH,
            "m_InternalIds": [
                internal_id(font_origin, font_path),
                internal_id(script_origin, SCRIPT_PATH),
                f"assets/{key}.asset",
                internal_id("runtime", "StandaloneWindows64/unrelated.bundle"),
            ],
            "m_ProviderIds": ["AssetBundleProvider", "BundledAssetProvider"],
            "m_resourceTypes": [
                {"m_ClassName": BUNDLE_RESOURCE},
                {"m_ClassName": "TMPro.TMP_FontAsset"},
            ],
            "m_KeyDataString": base64.b64encode(key_data).decode(),
            "m_BucketDataString": base64.b64encode(buckets).decode(),
            "m_EntryDataString": base64.b64encode(entries).decode(),
            "m_ExtraDataString": base64.b64encode(extra_data).decode(),
        }
    ).encode()


def _revision(route: str, payload: bytes, number: str = "1042") -> StoredRevision:
    return StoredRevision(
        id=uuid4(),
        route=route,
        game_version="3.2.0",
        revision=number,
        source_url=(
            f"https://cdn.example/prefix/{route}/3.2.0/{number}/Windows?token=source%2Fsignature"
        ),
        catalog_url=f"https://catalog.example/pinned/{number}/catalog.json?token=catalog%2Fsignature",
        catalog_sha256=hashlib.sha256(payload).hexdigest(),
        catalog_hash=CATALOG_HASH,
    )


class ReadOnlyConnection:
    def __init__(self, revision: StoredRevision | None) -> None:
        self.revision = revision
        self.queries = []

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, query, params) -> None:
        assert query.lstrip().startswith("SELECT")
        assert "processed_at IS NOT NULL" in query
        self.queries.append((query, params))

    def fetchone(self):
        return None if self.revision is None else astuple(self.revision)


def _client(
    revision: StoredRevision, payload: bytes, calls: list[str], *, catalog_hash: str = CATALOG_HASH
) -> GameSourceClient:
    source = GameSource(
        revision.route,
        revision.game_version,
        revision.revision,
        revision.source_url,
        revision.catalog_url,
    )

    def fetch(url: str, _timeout: float) -> bytes:
        calls.append(url)
        if url == revision.catalog_url:
            return payload
        if url == source.catalog_hash_url:
            return catalog_hash.encode()
        pytest.fail(f"planner must use only stored catalog URLs, got {url}")

    return GameSourceClient(fetch=fetch)


def _plan(
    tmp_path: Path,
    revision: StoredRevision,
    payload: bytes,
    *,
    route_config: str | Path | None = None,
    catalog_hash: str = CATALOG_HASH,
) -> tuple[SteamInputPlan, ReadOnlyConnection, list[str]]:
    conn = ReadOnlyConnection(revision)
    calls: list[str] = []
    plan = plan_steam_inputs(
        conn,
        revision_id=revision.id,
        route_config=route_config or ROOT / "routes" / f"{revision.route.lower()}.yaml",
        work_dir=tmp_path / "work",
        filelist_path=tmp_path / f"{revision.route.lower()}.txt",
        client=_client(revision, payload, calls, catalog_hash=catalog_hash),
    )
    return plan, conn, calls


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("font_origin", ["remote", "runtime"])
@pytest.mark.parametrize("script_origin", ["remote", "runtime"])
def test_plans_both_steam_routes_from_catalog_origins(
    tmp_path: Path, route: str, font_origin: str, script_origin: str
) -> None:
    payload = _catalog(route, font_origin=font_origin, script_origin=script_origin)
    revision = _revision(route, payload)
    plan, conn, calls = _plan(tmp_path, revision, payload)
    root = ROUTES[route][0]
    expected_runtime = tuple(
        sorted(
            path
            for origin, path in [(font_origin, FONT_PATH), (script_origin, SCRIPT_PATH)]
            if origin == "runtime"
        )
    )
    assert plan.route == route
    assert plan.game_version == revision.game_version
    assert plan.revision == revision.revision
    assert plan.game_data_path == root
    assert plan.runtime_bundles == expected_runtime
    assert plan.filelist == (
        f"{root}/data.unity3d",
        *(f"{root}/StreamingAssets/aa/{path}" for path in expected_runtime),
    )
    assert plan.catalog_sha256 == revision.catalog_sha256
    assert plan.catalog_hash == revision.catalog_hash
    assert json.loads(json.dumps(asdict(plan)))["filelist"] == list(plan.filelist)
    assert (tmp_path / f"{route.lower()}.txt").read_bytes() == (
        "\n".join(plan.filelist) + "\n"
    ).encode()
    assert len(conn.queries) == 1
    assert conn.queries[0][1] == (revision.id,)
    assert calls[0] == revision.catalog_url
    assert calls[1] == revision.source_url.replace("/Windows?", "/Windows/catalog_3.2.0.hash?")


@pytest.mark.parametrize("route", ROUTES)
def test_deduplicates_recursive_dependencies_and_cycles(tmp_path: Path, route: str) -> None:
    payload = _catalog(route, cycle=True)
    plan, _, _ = _plan(tmp_path, _revision(route, payload), payload)
    assert plan.runtime_bundles == tuple(sorted((FONT_PATH, SCRIPT_PATH)))
    assert len(plan.filelist) == 3


@pytest.mark.parametrize("route", ROUTES)
def test_origin_switches_replace_stale_filelist_and_never_copy_unrelated_bundles(
    tmp_path: Path, route: str
) -> None:
    old_payload = _catalog(route, font_origin="runtime", font_path="StandaloneWindows64/old.bundle")
    old_plan, _, _ = _plan(tmp_path, _revision(route, old_payload), old_payload)
    remote_payload = _catalog(route, font_origin="remote")
    remote_plan, _, _ = _plan(tmp_path, _revision(route, remote_payload, "1043"), remote_payload)
    assert remote_plan.runtime_bundles == (SCRIPT_PATH,)
    assert "old.bundle" not in (tmp_path / f"{route.lower()}.txt").read_text()
    local_payload = _catalog(route, font_origin="runtime")
    local_plan, _, _ = _plan(tmp_path, _revision(route, local_payload, "1044"), local_payload)
    assert FONT_PATH in local_plan.runtime_bundles
    assert old_plan.filelist != remote_plan.filelist != local_plan.filelist
    assert "unrelated.bundle" not in "\n".join(local_plan.filelist)


def test_shared_work_directory_keeps_per_route_catalogs_and_filelists(tmp_path: Path) -> None:
    plans = []
    for route in ROUTES:
        payload = _catalog(route)
        plan, _, _ = _plan(tmp_path, _revision(route, payload), payload)
        plans.append(plan)
        assert (
            tmp_path / "work" / route.lower() / "catalog" / "catalog.json"
        ).read_bytes() == payload
    for plan in plans:
        assert (tmp_path / f"{plan.route.lower()}.txt").read_text().splitlines() == list(
            plan.filelist
        )
    assert set(plans[0].filelist).isdisjoint(plans[1].filelist)


@pytest.mark.parametrize("changed", ["sha256", "compatibility_hash"])
def test_rejects_stale_catalog_without_replacing_filelist(tmp_path: Path, changed: str) -> None:
    payload = _catalog("INT_STEAM")
    revision = _revision("INT_STEAM", payload)
    filelist = tmp_path / "int_steam.txt"
    filelist.write_bytes(b"previous-plan\n")
    if changed == "sha256":
        revision = replace(revision, catalog_sha256="0" * 64)
    actual_hash = "0" * 32 if changed == "compatibility_hash" else CATALOG_HASH
    message = "SHA-256 changed" if changed == "sha256" else "compatibility hash changed"
    with pytest.raises(RuntimeError, match=message):
        _plan(tmp_path, revision, payload, catalog_hash=actual_hash)
    assert filelist.read_bytes() == b"previous-plan\n"


def test_rejects_route_mismatch_before_fetching_catalog(tmp_path: Path) -> None:
    payload = _catalog("INT_STEAM")
    revision = _revision("INT_STEAM", payload)
    with pytest.raises(RuntimeError, match="route mismatch"):
        _plan(tmp_path, revision, payload, route_config=ROOT / "routes/cn_steam.yaml")
    assert not (tmp_path / "work").exists()
    assert not (tmp_path / "int_steam.txt").exists()


@pytest.mark.parametrize("route", ["INT_ANDROID", "CN_ANDROID"])
def test_planner_rejects_other_platform_routes(tmp_path: Path, route: str) -> None:
    payload = _catalog("INT_STEAM")
    revision = _revision(route, payload)
    with pytest.raises(ValueError, match="Windows Steam route"):
        _plan(tmp_path, revision, payload)
    assert not (tmp_path / "work").exists()


def test_rejects_steam_config_with_non_windows_platform(tmp_path: Path) -> None:
    config = yaml.safe_load((ROOT / "routes/int_steam.yaml").read_text())
    config["platform"] = "android"
    config_path = tmp_path / "wrong-platform.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    payload = _catalog("INT_STEAM")
    with pytest.raises(ValueError, match="Windows Steam route"):
        _plan(tmp_path, _revision("INT_STEAM", payload), payload, route_config=config_path)


def test_missing_processed_revision_does_not_fetch_or_write(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="processed revision not found"):
        plan_steam_inputs(
            ReadOnlyConnection(None),
            revision_id=uuid4(),
            route_config=ROOT / "routes/int_steam.yaml",
            work_dir=tmp_path / "work",
            filelist_path=tmp_path / "filelist.txt",
        )
    assert not (tmp_path / "work").exists()
    assert not (tmp_path / "filelist.txt").exists()


@pytest.mark.parametrize("missing", ["key", "dependencies"])
def test_requires_configured_tmp_catalog_target(tmp_path: Path, missing: str) -> None:
    payload = _catalog(
        "INT_STEAM", missing_key=missing == "key", missing_dependencies=missing == "dependencies"
    )
    with pytest.raises((KeyError, ValueError), match="catalog key"):
        _plan(tmp_path, _revision("INT_STEAM", payload), payload)
    assert not (tmp_path / "int_steam.txt").exists()


@pytest.mark.parametrize("path", ["../escape.bundle", "/absolute.bundle", "x.bundle?query=bad"])
def test_rejects_unsafe_runtime_dependencies_before_writing_filelist(
    tmp_path: Path, path: str
) -> None:
    payload = _catalog("INT_STEAM", font_path=path)
    with pytest.raises(RuntimeBundleError, match="unsafe runtime bundle path"):
        _plan(tmp_path, _revision("INT_STEAM", payload), payload)
    assert not (tmp_path / "int_steam.txt").exists()
