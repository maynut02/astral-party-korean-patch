from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import struct
import sys
import zipfile
from pathlib import Path

import pytest
from test_addressables_catalog import _catalog_json

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/workflow/manual_patch.py"
SPEC = importlib.util.spec_from_file_location("manual_patch", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

REMOTE_PREFIX = "{App.WebServerConfig.Path}/"
RUNTIME_PREFIX = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/"
RUNTIME_PATH = "StandaloneWindows64/actual-catalog-name.bundle"
STEAM_LAYOUTS = {
    "INT_STEAM": ("AstralParty_INT", "8vJXnINT/AstralParty_INT_Data"),
    "CN_STEAM": ("AstralParty_CN", "8vJXn6CN/AstralParty_CN_Data"),
}


def _write_catalog(
    tmp_path: Path,
    *locations: tuple[str, str, str],
    dependencies: bool = False,
) -> Path:
    """Reuse the encoded catalog fixture with real bundle options and location rows."""
    data = json.loads(_catalog_json())
    template = base64.b64decode(data["m_ExtraDataString"])
    # JsonObject: type, length-prefixed assembly/class names, then UTF-16 JSON.
    header_end = 2 + template[1]
    header_end += 1 + template[header_end]
    extra = bytearray()
    rows = []
    for index, (name, bundle_hash, _catalog_id) in enumerate(locations):
        options = json.dumps(
            {"m_BundleName": name, "m_Hash": bundle_hash, "m_BundleSize": 12345, "m_Crc": 0}
        ).encode("utf-16-le")
        dependency_key = 1 if dependencies and index == 0 else -1
        rows.append((index, 0, dependency_key, 0, len(extra), 0, 0))
        extra.extend(template[:header_end] + struct.pack("<i", len(options)) + options)
    # Retain the fixture's TextAsset and its bundle dependency.
    rows.append((len(locations), 1, 0, 0, -1, 1, 1))
    buckets = base64.b64decode(data["m_BucketDataString"])
    key_offsets = [struct.unpack_from("<i", buckets, offset)[0] for offset in (4, 16)]
    dependency_indices = list(range(1, len(locations))) if dependencies else [len(locations)]
    bucket_data = bytearray(struct.pack("<i", 2))
    for offset, indices in zip(key_offsets, [[0], dependency_indices], strict=True):
        bucket_data.extend(struct.pack("<ii", offset, len(indices)))
        for index in indices:
            bucket_data.extend(struct.pack("<i", index))
    data["m_InternalIds"] = [row[2] for row in locations] + ["english-internal-id"]
    data["m_EntryDataString"] = base64.b64encode(
        struct.pack("<i", len(rows)) + b"".join(struct.pack("<7i", *row) for row in rows)
    ).decode()
    data["m_ExtraDataString"] = base64.b64encode(extra).decode()
    data["m_BucketDataString"] = base64.b64encode(bucket_data).decode()
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _bundle_path(route: str, origin: str, *, relative: str = RUNTIME_PATH) -> str:
    locallow, game_data = STEAM_LAYOUTS[route]
    if origin == "remote":
        return f"{locallow}/com.unity.addressables/AssetBundles/bundle-name/bundle-hash/__data"
    return f"{game_data}/StreamingAssets/aa/{relative}"


def _internal_id(origin: str, relative: str = RUNTIME_PATH) -> str:
    return (REMOTE_PREFIX if origin == "remote" else RUNTIME_PREFIX) + relative


def _write_manifest(tmp_path: Path, route: str) -> tuple[Path, Path, dict[str, bytes]]:
    payloads = tmp_path / "payloads"
    payloads.mkdir()
    raw = {
        "lang.bin": b"lang-patched",
        "data.unity3d": b"unity-patched",
    }
    for name, payload in raw.items():
        (payloads / name).write_bytes(payload)

    files = [
        {
            "target": "addressables",
            "path": "bundle-name/bundle-hash/__data",
            "downloadUrl": "https://example.test/lang.bin.gz",
            "sha256": hashlib.sha256(raw["lang.bin"]).hexdigest(),
            "size": len(raw["lang.bin"]),
        },
        {
            "target": "game-data",
            "path": "data.unity3d",
            "downloadUrl": "https://example.test/data.unity3d.gz",
            "sha256": hashlib.sha256(raw["data.unity3d"]).hexdigest(),
            "size": len(raw["data.unity3d"]),
        },
    ]
    manifest = {
        "schemaVersion": 2,
        "patch": {"route": route, "version": "v3.2.0_r116_p5"},
        "game": {"version": "3.2.0", "revision": "116"},
        "files": files,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, payloads, raw


@pytest.mark.parametrize(
    ("route", "locallow", "executable", "data_dir"),
    [
        ("INT_STEAM", "AstralParty_INT", "8vJXnINT", "AstralParty_INT_Data"),
        ("CN_STEAM", "AstralParty_CN", "8vJXn6CN", "AstralParty_CN_Data"),
    ],
)
@pytest.mark.parametrize("origin", ["remote", "runtime"])
def test_builds_manual_zip_with_install_ready_tree(
    tmp_path: Path,
    route: str,
    locallow: str,
    executable: str,
    data_dir: str,
    origin: str,
) -> None:
    manifest, payloads, raw = _write_manifest(tmp_path, route)
    catalog = _write_catalog(
        tmp_path, ("bundle-name", "bundle-hash", _internal_id(origin))
    )
    manifest_before = manifest.read_bytes()
    output = tmp_path / f"{route}_manual_patch.zip"

    assert MODULE.build_manual_patch(manifest, payloads, output, catalog_path=catalog) == output

    with zipfile.ZipFile(output) as archive:
        assert sorted(archive.namelist()) == sorted(
            ["설치방법.txt", _bundle_path(route, origin), f"{executable}/{data_dir}/data.unity3d"]
        )
        assert archive.read(_bundle_path(route, origin)) == raw["lang.bin"]
        assert archive.read(f"{executable}/{data_dir}/data.unity3d") == raw["data.unity3d"]
        readme = archive.read("설치방법.txt").decode("utf-8")
        assert readme == MODULE._readme(route, json.loads(manifest_before))
        assert f"Astral Party 한국어 패치 - {route} 수동 설치" in readme
        assert locallow in readme
        assert executable in readme
        assert "WindowsPatcher" in readme
    assert manifest.read_bytes() == manifest_before


def test_builds_android_manual_zip_with_install_ready_tree(tmp_path: Path) -> None:
    manifest, payloads, raw = _write_manifest(tmp_path, "INT_ANDROID")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["files"] = [manifest_data["files"][0]]
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    output = tmp_path / "INT_ANDROID_manual_patch.zip"

    MODULE.build_manual_patch(manifest, payloads, output)

    package = "com.feimo.astralpartyjpn"
    with zipfile.ZipFile(output) as archive:
        assert sorted(archive.namelist()) == sorted(
            [
                "설치방법.txt",
                f"{package}/files/com.unity.addressables/AssetBundles/"
                "bundle-name/bundle-hash/__data",
            ]
        )
        assert (
            archive.read(
                f"{package}/files/com.unity.addressables/AssetBundles/"
                "bundle-name/bundle-hash/__data"
            )
            == raw["lang.bin"]
        )
        readme = archive.read("설치방법.txt").decode("utf-8")
        assert readme == MODULE._readme("INT_ANDROID", manifest_data)
        assert "/storage/emulated/0/Android/data/" in readme
        assert package in readme
        assert "__data__" in readme
        assert "AndroidPatcher" in readme


def test_builds_cn_android_manual_zip_without_duplicate_payload(tmp_path: Path) -> None:
    manifest, payloads, raw = _write_manifest(tmp_path, "CN_ANDROID")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["files"] = [manifest_data["files"][0]]
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    output = tmp_path / "CN_ANDROID_manual_patch.zip"

    MODULE.build_manual_patch(manifest, payloads, output)

    with zipfile.ZipFile(output) as archive:
        payload_name = "files/com.unity.addressables/AssetBundles/bundle-name/bundle-hash/__data"
        assert sorted(archive.namelist()) == sorted(["설치방법.txt", payload_name])
        assert archive.read(payload_name) == raw["lang.bin"]
        payload_entries = [name for name in archive.namelist() if name.endswith("/__data")]
        assert payload_entries == [payload_name]
        assert not any(name.startswith("CN_ANDROID/") for name in archive.namelist())
        readme = archive.read("설치방법.txt").decode("utf-8")
        assert readme == MODULE._readme("CN_ANDROID", manifest_data)
        assert "com.feimo.astralparty" in readme
        assert "com.feimo.astralparty.bilibili" in readme
        assert "압축파일 안의 files 폴더" in readme
        assert "동일한 패치 payload" in readme
        assert "CN_ANDROID 전용" in readme


def test_rejects_android_game_data_target(tmp_path: Path) -> None:
    manifest, payloads, _ = _write_manifest(tmp_path, "INT_ANDROID")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["files"] = [manifest_data["files"][1]]
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported manual patch target/path"):
        MODULE.build_manual_patch(manifest, payloads, tmp_path / "android.zip")


def test_rejects_payload_that_does_not_match_manifest(tmp_path: Path) -> None:
    manifest, payloads, _ = _write_manifest(tmp_path, "INT_STEAM")
    catalog = _write_catalog(
        tmp_path, ("bundle-name", "bundle-hash", _internal_id("remote"))
    )
    (payloads / "lang.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="payload size mismatch|payload SHA-256 mismatch"):
        MODULE.build_manual_patch(manifest, payloads, tmp_path / "int.zip", catalog_path=catalog)


@pytest.mark.parametrize("route", STEAM_LAYOUTS)
def test_steam_requires_catalog_before_creating_output(tmp_path: Path, route: str) -> None:
    manifest, payloads, _ = _write_manifest(tmp_path, route)
    output = tmp_path / "new-output" / "manual.zip"

    with pytest.raises(ValueError, match="catalog is required"):
        MODULE.build_manual_patch(manifest, payloads, output)

    assert not output.parent.exists()


@pytest.mark.parametrize("route", STEAM_LAYOUTS)
def test_catalog_origin_switch_replaces_previous_zip(tmp_path: Path, route: str) -> None:
    manifest, payloads, raw = _write_manifest(tmp_path, route)
    output = tmp_path / "manual.zip"
    previous_readme = None
    for origin in ("remote", "runtime", "remote"):
        catalog = _write_catalog(
            tmp_path, ("bundle-name", "bundle-hash", _internal_id(origin))
        )
        MODULE.build_manual_patch(manifest, payloads, output, catalog_path=catalog)
        with zipfile.ZipFile(output) as archive:
            assert set(archive.namelist()) == {
                "설치방법.txt",
                _bundle_path(route, origin),
                f"{STEAM_LAYOUTS[route][1]}/data.unity3d",
            }
            assert archive.read(_bundle_path(route, origin)) == raw["lang.bin"]
            readme = archive.read("설치방법.txt")
            if previous_readme is not None:
                assert readme == previous_readme
            previous_readme = readme


@pytest.mark.parametrize("route", STEAM_LAYOUTS)
@pytest.mark.parametrize("origin", ["remote", "runtime"])
def test_maps_every_manifest_bundle_and_ignores_unpatched_catalog_dependencies(
    tmp_path: Path, route: str, origin: str
) -> None:
    manifest, payloads, raw = _write_manifest(tmp_path, route)
    script_payload = b"patched-script-bundle"
    (payloads / "scripts.bin").write_bytes(script_payload)
    # Even locally available dependency payloads must not be included implicitly.
    (payloads / "unpatched.bin").write_bytes(b"unpatched dependency")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"].append(
        {
            "target": "addressables",
            "path": "script-root/script-hash/__data",
            "downloadUrl": "https://example.test/scripts.bin.gz",
            "sha256": hashlib.sha256(script_payload).hexdigest(),
            "size": len(script_payload),
        }
    )
    manifest.write_text(json.dumps(data), encoding="utf-8")
    script_origin = "runtime" if origin == "remote" else "remote"
    script_relative = "StandaloneWindows64/scripts-current.bundle"
    catalog = _write_catalog(
        tmp_path,
        ("bundle-name", "bundle-hash", _internal_id(origin)),
        ("script-root", "script-hash", _internal_id(script_origin, script_relative)),
        ("bundle-name", "unpatched-hash", "https://unsupported.example/unpatched.bundle"),
        ("unpatched-name", "bundle-hash", _internal_id("runtime", "unused.bundle")),
        dependencies=True,
    )
    output = tmp_path / "manual.zip"

    MODULE.build_manual_patch(manifest, payloads, output, catalog_path=catalog)

    locallow, game_data = STEAM_LAYOUTS[route]
    script_path = (
        f"{game_data}/StreamingAssets/aa/{script_relative}"
        if script_origin == "runtime"
        else f"{locallow}/com.unity.addressables/AssetBundles/script-root/script-hash/__data"
    )
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "설치방법.txt", _bundle_path(route, origin), script_path, f"{game_data}/data.unity3d"
        }
        assert archive.read(_bundle_path(route, origin)) == raw["lang.bin"]
        assert archive.read(script_path) == script_payload
        assert archive.read(f"{game_data}/data.unity3d") == raw["data.unity3d"]


@pytest.mark.parametrize("origin", ["remote", "runtime"])
def test_accepts_duplicate_catalog_rows_with_same_destination(tmp_path: Path, origin: str) -> None:
    manifest, payloads, raw = _write_manifest(tmp_path, "INT_STEAM")
    row = ("bundle-name", "bundle-hash", _internal_id(origin))
    catalog = _write_catalog(tmp_path, row, row)
    output = tmp_path / "manual.zip"

    MODULE.build_manual_patch(manifest, payloads, output, catalog_path=catalog)

    with zipfile.ZipFile(output) as archive:
        expected = _bundle_path("INT_STEAM", origin)
        assert archive.namelist().count(expected) == 1
        assert len(archive.namelist()) == 3
        assert archive.read(expected) == raw["lang.bin"]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_internal_id("remote"), _internal_id("runtime")),
        (_internal_id("runtime"), _internal_id("remote")),
        (_internal_id("runtime"), _internal_id("runtime", "different.bundle")),
    ],
)
def test_rejects_ambiguous_identity_without_replacing_output(
    tmp_path: Path, first: str, second: str
) -> None:
    manifest, payloads, _ = _write_manifest(tmp_path, "INT_STEAM")
    catalog = _write_catalog(
        tmp_path, ("bundle-name", "bundle-hash", first), ("bundle-name", "bundle-hash", second)
    )
    output = tmp_path / "manual.zip"
    output.write_bytes(b"previous package")

    with pytest.raises(ValueError, match="ambiguous catalog bundle destination"):
        MODULE.build_manual_patch(manifest, payloads, output, catalog_path=catalog)

    assert output.read_bytes() == b"previous package"


@pytest.mark.parametrize(
    "identity",
    [
        "other-bundle/bundle-hash/__data",
        "bundle-name/other-hash/__data",
        "bundle-name/bundle-hash/__data__",
    ],
)
def test_requires_exact_catalog_bundle_identity(tmp_path: Path, identity: str) -> None:
    manifest, payloads, _ = _write_manifest(tmp_path, "INT_STEAM")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"][0]["path"] = identity
    manifest.write_text(json.dumps(data), encoding="utf-8")
    catalog = _write_catalog(
        tmp_path, ("bundle-name", "bundle-hash", _internal_id("remote"))
    )
    output = tmp_path / "new-output" / "manual.zip"

    with pytest.raises(ValueError, match="manifest bundles not found in catalog"):
        MODULE.build_manual_patch(manifest, payloads, output, catalog_path=catalog)

    assert not output.parent.exists()


def test_rejects_matching_bundle_with_unsupported_origin_before_output(tmp_path: Path) -> None:
    manifest, payloads, _ = _write_manifest(tmp_path, "INT_STEAM")
    catalog = _write_catalog(
        tmp_path, ("bundle-name", "bundle-hash", "https://unsupported.example/matching.bundle")
    )
    output = tmp_path / "new-output" / "manual.zip"

    with pytest.raises(ValueError, match="unsupported Addressables bundle internal id"):
        MODULE.build_manual_patch(manifest, payloads, output, catalog_path=catalog)

    assert not output.parent.exists()


@pytest.mark.parametrize(
    "relative",
    [
        "../escape.bundle",
        "/absolute.bundle",
        "StandaloneWindows64/../escape.bundle",
        "StandaloneWindows64//empty.bundle",
        "StandaloneWindows64/./dot.bundle",
        "C:/drive.bundle",
        "file.bundle?query=1",
        "file.bundle#fragment",
        "%2e%2e/escape.bundle",
        "folder./file.bundle",
        "folder /file.bundle",
        "file.bundle\x00",
    ],
)
def test_rejects_unsafe_catalog_runtime_path_before_output(tmp_path: Path, relative: str) -> None:
    manifest, payloads, _ = _write_manifest(tmp_path, "INT_STEAM")
    catalog = _write_catalog(
        tmp_path, ("bundle-name", "bundle-hash", _internal_id("runtime", relative))
    )
    output = tmp_path / "new-output" / "manual.zip"

    with pytest.raises(RuntimeError, match="unsafe runtime bundle path"):
        MODULE.build_manual_patch(manifest, payloads, output, catalog_path=catalog)

    assert not output.parent.exists()
