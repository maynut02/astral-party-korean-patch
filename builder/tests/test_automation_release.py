import json
from pathlib import Path

from astral_builder.automation.release import read_release_metadata, update_release_index
from astral_builder.release.index import ReleaseIndex


def _manifest(path: Path) -> Path:
    data = {
        "schemaVersion": 2,
        "patch": {
            "version": "v1",
            "channel": "release",
            "route": "INT_ANDROID",
            "buildId": "00000000-0000-0000-0000-000000000001",
            "translationFingerprint": "a" * 64,
        },
        "game": {
            "version": "3.2.0",
            "revision": "116",
            "catalogHash": "b" * 32,
        },
        "files": [
            {"target": "addressables", "path": "BundleA/hash/__data"},
            {"target": "game-data", "path": "Data/file.bin"},
            {"target": "addressables", "path": "BundleB/hash/__data"},
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_reads_release_metadata(tmp_path: Path) -> None:
    metadata = read_release_metadata(_manifest(tmp_path / "manifest.json"))
    assert metadata.patch_version == "v1"
    assert metadata.addressables_paths == (
        "BundleA/hash/__data",
        "BundleB/hash/__data",
    )


def test_updates_release_index_from_exact_manifest_bytes(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "manifest.json")
    index_path = tmp_path / "release-index.json"
    updated = update_release_index(
        manifest_path=manifest,
        manifest_url="https://example.test/v1/manifest.json",
        index_path=index_path,
    )
    assert len(updated.releases) == 1
    assert updated.releases[0].addressables_paths == (
        "BundleA/hash/__data",
        "BundleB/hash/__data",
    )
    raw = json.loads(index_path.read_text())
    assert raw["releases"][0]["addressablesPaths"] == [
        "BundleA/hash/__data",
        "BundleB/hash/__data",
    ]
    assert ReleaseIndex.from_json(index_path.read_text()) == updated


def test_non_android_release_does_not_publish_watcher_paths(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "manifest.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["patch"]["route"] = "INT_STEAM"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    index_path = tmp_path / "release-index.json"

    updated = update_release_index(
        manifest_path=manifest,
        manifest_url="https://example.test/v1/manifest.json",
        index_path=index_path,
    )

    assert updated.releases[0].addressables_paths == ()
    raw = json.loads(index_path.read_text())
    assert "addressablesPaths" not in raw["releases"][0]
