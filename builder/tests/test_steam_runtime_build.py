from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from astral_builder.addressables.catalog import (
    AddressablesCatalog,
    CatalogLocation,
    SerializedJsonObject,
    _Bucket,
)
from astral_builder.addressables.resolver import BundleOrigin, ResolvedBundle
from astral_builder.automation import build as build_module
from astral_builder.automation.build import StoredBundle, StoredRevision
from astral_builder.automation.sync import load_route_sync_config
from astral_builder.database.snapshot import make_snapshot
from astral_builder.game.source import DownloadedCatalog
from astral_builder.source import runtime as runtime_module
from astral_builder.source.downloader import RemoteBundleDownloader

ROOT = Path(__file__).resolve().parents[2]
REMOTE = BundleOrigin.REMOTE
RUNTIME = BundleOrigin.RUNTIME
STEAM_ROUTES = ("INT_STEAM", "CN_STEAM")
MOBILE_ROUTES = ("INT_ANDROID", "CN_ANDROID")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _Bundle:
    role: str
    logical_name: str
    origin: BundleOrigin
    bundle_name: str
    cache_hash: str
    payload: bytes
    dependency_key: str | None = None

    @property
    def relative_path(self) -> str:
        return f"StandaloneWindows64/{self.bundle_name}_{self.cache_hash}.bundle"

    @property
    def internal_id(self) -> str:
        prefix = (
            "{App.WebServerConfig.Path}"
            if self.origin is REMOTE
            else "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
        )
        return f"{prefix}/{self.relative_path}"


class _Build:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
        self.root = root
        self.route_config = ROOT / "routes" / f"{route.lower()}.yaml"
        self.config = load_route_sync_config(self.route_config)
        self.game_data_dir = root / "AstralParty_Data"
        self.resources = root / "resources"
        self.resources.mkdir(parents=True)
        for name in ("tmp-font.dat", "tmp-font-atlas.png", "legacy-font.ttf"):
            (self.resources / name).write_bytes(name.encode())
        self.legacy_data = root / "data.unity3d"
        self.legacy_data.write_bytes(b"legacy source")
        self.revision = StoredRevision(
            uuid4(),
            route,
            "3.3.0",
            "catalog-r1",
            "https://example.test/content",
            "https://example.test/catalog.json",
            "a" * 64,
            "c" * 32,
        )
        self.snapshot = make_snapshot(
            revision_id=str(uuid4()),
            route="INT_STEAM",
            game_version="3.3.0",
            revision="canonical-r1",
            locale="ko",
            units=(),
        )
        self.requests: list[str] = []
        self.runtime_calls: list[tuple[ResolvedBundle, Path]] = []
        self.patch_calls: list[tuple[Path, dict]] = []
        self.catalog_reads: list[Path] = []
        self.hash_reads: list[str] = []
        self.started: list[dict] = []
        self.recorded: list[tuple] = []
        self.statuses: list[str] = []
        self.catalog_sha_override: str | None = None
        self.catalog_hash_override: str | None = None
        self.bundle_downloader = RemoteBundleDownloader(fetch=self.fetch)

        monkeypatch.setattr(build_module, "load_stored_revision", lambda *_: self.revision)
        monkeypatch.setattr(build_module, "load_stored_bundles", lambda *_: self.stored)
        monkeypatch.setattr(
            build_module, "load_latest_translation_snapshot", lambda *_, **__: self.snapshot
        )

        def read_catalog(path):
            self.catalog_reads.append(path)
            assert path.read_bytes() == self.catalog_payload
            return self.catalog

        monkeypatch.setattr(build_module.AddressablesCatalog, "from_path", read_catalog)
        runtime_loader = build_module.load_runtime_bundle

        def load_runtime(bundle, game_data_dir):
            self.runtime_calls.append((bundle, game_data_dir))
            return runtime_loader(bundle, game_data_dir)

        monkeypatch.setattr(build_module, "load_runtime_bundle", load_runtime)

        def load_unity(payload):
            names = json.loads(payload).get("AssetBundle", [])
            return SimpleNamespace(
                objects=[
                    SimpleNamespace(
                        type=SimpleNamespace(name="AssetBundle"),
                        read=lambda name=name: SimpleNamespace(m_Name=name),
                    )
                    for name in names
                ]
            )

        monkeypatch.setattr(runtime_module.UnityPy, "load", load_unity)
        monkeypatch.setattr(build_module, "extract_text_assets", self.text_assets)
        monkeypatch.setattr(build_module, "extract_object_names", self.object_names)
        monkeypatch.setattr(build_module, "patch_lang_bundle", self.patch)
        monkeypatch.setattr(build_module, "patch_str_bundle", self.patch)
        monkeypatch.setattr(build_module, "patch_tmp_font_bundle", self.patch_tmp)
        monkeypatch.setattr(build_module, "patch_legacy_font", self.patch)
        monkeypatch.setattr(build_module, "validate_file", lambda _: None)

        def begin(_conn, **kwargs):
            self.started.append(kwargs)
            return SimpleNamespace(id=uuid4())

        monkeypatch.setattr(build_module, "begin_build", begin)
        monkeypatch.setattr(
            build_module,
            "record_build_files",
            lambda _conn, _id, files: self.recorded.append(files),
        )
        monkeypatch.setattr(
            build_module,
            "set_build_status",
            lambda _conn, _id, status: self.statuses.append(status),
        )
        self.set_sources(REMOTE)

    def set_sources(
        self,
        font_origin: BundleOrigin,
        *,
        script_origin: BundleOrigin | None = None,
        generation: int = 1,
        font_objects: tuple[dict[str, list[str]], ...] | None = None,
        font_origins: tuple[BundleOrigin, ...] | None = None,
    ) -> None:
        config = self.config
        if font_objects is None:
            font_objects = (
                {
                    "MonoBehaviour": [config.tmp_asset_name],
                    "Texture2D": [f"{config.tmp_asset_name} Atlas"],
                },
            )
        specs = [
            (
                "lang",
                f"lang:{config.lang_target}",
                REMOTE,
                {"TextAsset": [config.lang_assets[config.lang_target]]},
                None,
            ),
            ("str", "str", REMOTE, {"TextAsset": ["STRCard"]}, None),
        ]
        if script_origin is not None:
            specs.append(
                ("script", "tmp-font", script_origin, {"MonoScript": ["TMP_FontAsset"]}, "script")
            )
        for index, objects in enumerate(font_objects):
            origin = font_origins[index] if font_origins else font_origin
            specs.append((f"font-{index}", "tmp-font", origin, objects, "font"))

        locations = []
        buckets: dict[str, list[int]] = {}
        self.bundles: dict[str, _Bundle] = {}
        self.remote_payloads: dict[str, bytes] = {}
        stored = []
        for role, logical, origin, objects, group in specs:
            bundle_name = f"catalog-{generation}-{role}"
            bundle = _Bundle(
                role,
                logical,
                origin,
                bundle_name,
                _sha256(f"{generation}-{role}".encode())[:32],
                json.dumps(objects | {"AssetBundle": [bundle_name]}, sort_keys=True).encode(),
                "script" if group == "font" and script_origin is not None else None,
            )
            self.bundles[role] = bundle
            options = SerializedJsonObject(
                "Unity.ResourceManager",
                "AssetBundleRequestOptions",
                (
                    json.dumps(
                        {
                            "m_BundleName": bundle.bundle_name,
                            "m_Hash": bundle.cache_hash,
                            "m_BundleSize": len(bundle.payload),
                            "m_Crc": 0,
                        }
                    )
                ),
            )
            location = CatalogLocation(
                len(locations),
                bundle.internal_id,
                "AssetBundleProvider",
                bundle.dependency_key,
                0,
                bundle.relative_path,
                "UnityEngine.ResourceManagement.ResourceProviders.IAssetBundleResource",
                options,
            )
            buckets.setdefault(group or role, []).append(location.index)
            locations.append(location)
            if origin is REMOTE:
                url = f"{self.revision.source_url}/{bundle.relative_path}"
                self.remote_payloads[url] = bundle.payload
                stored.append(
                    StoredBundle(
                        logical,
                        bundle.bundle_name,
                        bundle.cache_hash,
                        _sha256(bundle.payload),
                        len(bundle.payload),
                    )
                )
            else:
                local = self.runtime_path(bundle)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(bundle.payload)
        for key, dep, type_name in (
            (config.lang_assets[config.lang_target], "lang", "TextAsset"),
            (config.str_catalog_key, "str", "TextAsset"),
            (config.tmp_catalog_key, "font", "MonoBehaviour"),
        ):
            buckets[key] = [len(locations)]
            locations.append(
                CatalogLocation(
                    len(locations),
                    f"Assets/{key}",
                    "BundledAssetProvider",
                    dep,
                    0,
                    key,
                    f"UnityEngine.{type_name}",
                )
            )
        self.catalog = AddressablesCatalog(
            locator_id="fixture",
            build_result_hash=self.revision.catalog_hash,
            keys=tuple(buckets),
            buckets=tuple(_Bucket(0, tuple(v)) for v in buckets.values()),
            locations=tuple(locations),
        )
        self.catalog_payload = json.dumps(
            [bundle.internal_id for bundle in self.bundles.values()]
        ).encode()
        self.revision = replace(
            self.revision,
            revision=f"catalog-r{generation}",
            catalog_sha256=_sha256(self.catalog_payload),
        )
        self.stored = tuple(stored)

    def runtime_path(self, bundle: _Bundle) -> Path:
        return self.game_data_dir / "StreamingAssets" / "aa" / bundle.relative_path

    def fetch(self, url: str, _timeout: float) -> bytes:
        self.requests.append(url)
        return self.remote_payloads[url]

    def download_catalog(self, source, destination) -> DownloadedCatalog:
        assert source.route == self.config.route
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.catalog_payload)
        return DownloadedCatalog(
            destination,
            self.catalog_sha_override or _sha256(self.catalog_payload),
            len(self.catalog_payload),
        )

    def fetch_catalog_hash(self, source) -> str:
        self.hash_reads.append(source.route)
        return self.catalog_hash_override or self.revision.catalog_hash

    @staticmethod
    def text_assets(path: Path, *, name_prefix: str | None = None) -> dict[str, bytes]:
        names = json.loads(path.read_bytes()).get("TextAsset", [])
        return {
            name: b"source" for name in names if name_prefix is None or name.startswith(name_prefix)
        }

    @staticmethod
    def object_names(path: Path, *, type_name: str) -> tuple[str, ...]:
        return tuple(json.loads(path.read_bytes()).get(type_name, []))

    @staticmethod
    def patch(source, destination, *_args, **_kwargs) -> None:
        destination.write_bytes(b"patched:" + Path(source).read_bytes())

    def patch_tmp(self, source, destination, **kwargs) -> None:
        self.patch_calls.append((source, kwargs))
        self.patch(source, destination)

    def build(self, **kwargs):
        return build_module.build_patch(
            object(),
            revision_id=self.revision.id,
            route_config=self.route_config,
            work_dir=self.root / "work",
            output_dir=self.root / "output",
            asset_base_url="https://example.test/release",
            source_asset_base_url="https://example.test/originals",
            patch_version="local",
            legacy_data_path=self.legacy_data,
            resources_root=self.resources,
            client=self,
            downloader=self.bundle_downloader,
            **kwargs,
        )


@pytest.fixture
def make_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return lambda route: _Build(tmp_path, monkeypatch, route)


@pytest.mark.parametrize("route", STEAM_ROUTES)
@pytest.mark.parametrize("origin", (REMOTE, RUNTIME))
@pytest.mark.parametrize("script_origin", (None, REMOTE, RUNTIME))
def test_steam_tmp_uses_catalog_sources_and_preserves_manifest_metadata(
    make_build,
    route,
    origin,
    script_origin,
) -> None:
    build = make_build(route)
    build.set_sources(origin, script_origin=script_origin)
    kwargs = {}
    if RUNTIME in (origin, script_origin):
        kwargs["steam_data_dir"] = str(build.game_data_dir)
    result = build.build(**kwargs)

    font = build.bundles["font-0"]
    item = result.files[2]
    assert len(result.files) == 4
    assert result.translation_fingerprint == build.snapshot.fingerprint
    assert item.path == f"{font.bundle_name}/{font.cache_hash}/__data"
    assert item.source_sha256 == _sha256(font.payload)
    assert item.source_size == len(font.payload)
    source_archive = build.root / "output/originals" / item.source_download_url.rsplit("/", 1)[1]
    assert gzip.decompress(source_archive.read_bytes()) == font.payload
    assert item.source_download_sha256 == _sha256(source_archive.read_bytes())
    assert item.source_download_size == source_archive.stat().st_size
    archive = build.root / "output/assets" / item.download_url.rsplit("/", 1)[1]
    assert gzip.decompress(archive.read_bytes()) == b"patched:" + font.payload
    assert item.sha256 == _sha256(b"patched:" + font.payload)
    expected_source = (
        build.runtime_path(font)
        if origin is RUNTIME
        else build.root / "work/bundles" / font.cache_hash / f"{font.cache_hash}.bundle"
    )
    assert build.patch_calls == [
        (
            expected_source,
            {
                "mono_name": build.config.tmp_asset_name,
                "mono_payload": b"tmp-font.dat",
                "texture_name": f"{build.config.tmp_asset_name} Atlas",
                "atlas_png": b"tmp-font-atlas.png",
            },
        )
    ]
    assert expected_source.read_bytes() == font.payload
    assert set(build.requests) == set(build.remote_payloads)
    assert len(build.requests) == len(build.remote_payloads)
    assert {bundle.bundle_name for bundle, _ in build.runtime_calls} == {
        bundle.bundle_name for bundle in build.bundles.values() if bundle.origin is RUNTIME
    }
    assert all(root == build.game_data_dir for _, root in build.runtime_calls)
    assert build.statuses == ["validated"]
    assert build.recorded[0][2].relative_path == item.path
    manifest = json.loads(result.manifest.read_bytes())
    assert manifest["game"]["catalogHash"] == build.revision.catalog_hash
    assert manifest["files"][2]["sourceSha256"] == item.source_sha256


@pytest.mark.parametrize("route", STEAM_ROUTES)
@pytest.mark.parametrize("origins", ((REMOTE, RUNTIME, REMOTE), (RUNTIME, REMOTE, RUNTIME)))
def test_steam_re_resolves_tmp_on_every_build_through_source_transitions(
    make_build,
    route,
    origins,
) -> None:
    build = make_build(route)
    paths = []
    for generation, origin in enumerate(origins, start=1):
        build.set_sources(origin, generation=generation)
        result = build.build(steam_data_dir=build.game_data_dir)
        font = build.bundles["font-0"]
        assert result.files[2].path == f"{font.bundle_name}/{font.cache_hash}/__data"
        paths.append(result.files[2].path)
    assert len(set(paths)) == 3
    assert len(build.catalog_reads) == 3
    assert build.hash_reads == [route] * 3
    assert len(build.runtime_calls) == origins.count(RUNTIME)


@pytest.mark.parametrize("route", STEAM_ROUTES)
@pytest.mark.parametrize("runtime_role", ("font", "script"))
def test_steam_requires_data_directory_for_any_runtime_tmp_dependency(
    make_build,
    route,
    runtime_role,
) -> None:
    build = make_build(route)
    build.set_sources(RUNTIME if runtime_role == "font" else REMOTE, script_origin=RUNTIME)
    with pytest.raises(RuntimeError) as error:
        build.build()
    message = str(error.value)
    assert "steam_data_dir" in message
    assert "--steam-data-dir" in message
    assert "StreamingAssets/aa" in message
    assert build.bundles["font-0" if runtime_role == "font" else "script"].internal_id in message
    assert not build.patch_calls
    assert not build.started


@pytest.mark.parametrize("route", STEAM_ROUTES)
@pytest.mark.parametrize("missing", ("directory", "file"))
@pytest.mark.parametrize("role", ("font-0", "script"))
def test_steam_missing_runtime_input_identifies_required_path(
    make_build, route, missing, role
) -> None:
    build = make_build(route)
    build.set_sources(RUNTIME if role == "font-0" else REMOTE, script_origin=RUNTIME)
    expected = build.runtime_path(build.bundles[role])
    if missing == "file":
        expected.unlink()
        data_dir = build.game_data_dir
    else:
        data_dir = build.root / "missing-game-data"
    with pytest.raises(runtime_module.RuntimeBundleError) as error:
        build.build(steam_data_dir=data_dir)
    message = str(error.value)
    missing_path = str(expected if missing == "file" else data_dir)
    assert missing_path in message or repr(missing_path) in message
    assert not build.patch_calls
    assert not build.started


@pytest.mark.parametrize("route", MOBILE_ROUTES)
@pytest.mark.parametrize("origin", (REMOTE, RUNTIME))
def test_mobile_keeps_remote_only_tmp_even_with_steam_data_dir(make_build, route, origin) -> None:
    build = make_build(route)
    build.set_sources(origin, script_origin=RUNTIME)
    if origin is RUNTIME:
        with pytest.raises(RuntimeError, match="found 0"):
            build.build(steam_data_dir=build.game_data_dir)
        assert not build.patch_calls
    else:
        result = build.build(steam_data_dir=build.game_data_dir)
        assert result.files[2].source_sha256 == _sha256(build.bundles["font-0"].payload)
        assert len(result.files) == 3
    assert not build.runtime_calls


@pytest.mark.parametrize("route", STEAM_ROUTES)
@pytest.mark.parametrize("objects", ("zero", "split", "multiple"))
def test_steam_requires_one_bundle_with_both_named_tmp_objects(make_build, route, objects) -> None:
    build = make_build(route)
    mono = {"MonoBehaviour": [build.config.tmp_asset_name]}
    atlas = {"Texture2D": [f"{build.config.tmp_asset_name} Atlas"]}
    candidates = {
        "zero": ({"MonoBehaviour": ["Wrong_TMP"], "Texture2D": ["Wrong_TMP Atlas"]},),
        "split": (mono, atlas),
        "multiple": (mono | atlas, mono | atlas),
    }
    build.set_sources(RUNTIME, font_objects=candidates[objects])
    with pytest.raises(RuntimeError) as error:
        build.build(steam_data_dir=build.game_data_dir)
    message = str(error.value)
    assert "expected exactly one bundle" in message
    assert f"MonoBehaviour {build.config.tmp_asset_name!r}" in message
    assert f"Texture2D {build.config.tmp_asset_name + ' Atlas'!r}" in message
    assert f"found {2 if objects == 'multiple' else 0}" in message
    if objects == "multiple":
        assert all(build.bundles[role].internal_id in message for role in ("font-0", "font-1"))
    assert not build.patch_calls
    assert not build.started


@pytest.mark.parametrize("role", ("font-0", "script"))
@pytest.mark.parametrize("mismatch", ("missing", "ambiguous", "hash", "sha256", "size"))
def test_steam_verifies_stored_identity_for_all_remote_tmp_dependencies(
    make_build,
    role,
    mismatch,
) -> None:
    build = make_build("INT_STEAM")
    build.set_sources(REMOTE if role == "font-0" else RUNTIME, script_origin=REMOTE)
    bundle = build.bundles[role]
    row = next(row for row in build.stored if row.bundle_name == bundle.bundle_name)
    rest = tuple(item for item in build.stored if item is not row)
    if mismatch == "missing":
        build.stored = rest
    elif mismatch == "ambiguous":
        build.stored += (row,)
    elif mismatch == "hash":
        build.stored = rest + (replace(row, bundle_hash="f" * 32),)
    elif mismatch == "size":
        build.stored = rest + (replace(row, source_size=row.source_size + 1),)
    else:
        build.remote_payloads[f"{build.revision.source_url}/{bundle.relative_path}"] = (
            b"!" + bundle.payload[1:]
        )
    expected = (
        "immutable source bundle changed"
        if mismatch in {"sha256", "size"}
        else ("stored bundle identity missing or ambiguous")
    )
    with pytest.raises(RuntimeError, match=expected):
        build.build(steam_data_dir=build.game_data_dir)
    assert not build.patch_calls
    assert not build.started


@pytest.mark.parametrize("origin", (REMOTE, RUNTIME))
@pytest.mark.parametrize("mismatch", ("sha256", "compatibility_hash"))
def test_steam_still_checks_immutable_catalog_before_loading_sources(
    make_build,
    origin,
    mismatch,
) -> None:
    build = make_build("CN_STEAM")
    build.set_sources(origin)
    if mismatch == "sha256":
        build.catalog_sha_override = "f" * 64
    else:
        build.catalog_hash_override = "f" * 32
    with pytest.raises(
        RuntimeError,
        match=(
            "immutable catalog SHA-256" if mismatch == "sha256" else "catalog compatibility hash"
        ),
    ):
        build.build(steam_data_dir=build.game_data_dir)
    assert not build.catalog_reads
    assert not build.requests
    assert not build.runtime_calls
    assert not build.started


@pytest.mark.parametrize("route", STEAM_ROUTES)
@pytest.mark.parametrize("origins", ((REMOTE, RUNTIME), (RUNTIME, REMOTE)))
def test_steam_rejects_multiple_candidates_across_remote_and_runtime_sources(
    make_build,
    route,
    origins,
) -> None:
    build = make_build(route)
    objects = {
        "MonoBehaviour": [build.config.tmp_asset_name],
        "Texture2D": [f"{build.config.tmp_asset_name} Atlas"],
    }
    build.set_sources(origins[0], font_objects=(objects, objects), font_origins=origins)
    with pytest.raises(RuntimeError, match="found 2") as error:
        build.build(steam_data_dir=build.game_data_dir)
    for role in ("font-0", "font-1"):
        assert build.bundles[role].internal_id in str(error.value)
    assert not build.patch_calls
    assert not build.started
