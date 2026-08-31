from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/workflow/manual_patch.py"
SPEC = importlib.util.spec_from_file_location("manual_patch", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


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
def test_builds_manual_zip_with_install_ready_tree(
    tmp_path: Path,
    route: str,
    locallow: str,
    executable: str,
    data_dir: str,
) -> None:
    manifest, payloads, raw = _write_manifest(tmp_path, route)
    output = tmp_path / f"{route}_manual_patch.zip"

    MODULE.build_manual_patch(manifest, payloads, output)

    with zipfile.ZipFile(output) as archive:
        assert archive.read(
            f"{locallow}/com.unity.addressables/AssetBundles/bundle-name/bundle-hash/__data"
        ) == raw["lang.bin"]
        assert archive.read(f"{executable}/{data_dir}/data.unity3d") == raw["data.unity3d"]
        readme = archive.read("설치방법.txt").decode("utf-8")
        assert locallow in readme
        assert executable in readme
        assert "WindowsPatcher" in readme


def test_rejects_android_manual_package(tmp_path: Path) -> None:
    manifest, payloads, _ = _write_manifest(tmp_path, "INT_ANDROID")
    with pytest.raises(ValueError, match="only supported for Steam routes"):
        MODULE.build_manual_patch(manifest, payloads, tmp_path / "android.zip")


def test_rejects_payload_that_does_not_match_manifest(tmp_path: Path) -> None:
    manifest, payloads, _ = _write_manifest(tmp_path, "INT_STEAM")
    (payloads / "lang.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="payload size mismatch|payload SHA-256 mismatch"):
        MODULE.build_manual_patch(manifest, payloads, tmp_path / "int.zip")
