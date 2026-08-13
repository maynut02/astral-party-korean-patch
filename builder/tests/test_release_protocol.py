import json
from pathlib import Path

import jsonschema
import pytest

from astral_builder.release.index import ReleaseIndex, ReleaseIndexEntry, manifest_digest
from astral_builder.release.manifest import ManifestFile, PatchManifest, PatchMetadata, TargetGame
from astral_builder.release.transport import gzip_payload

ROOT = Path(__file__).resolve().parents[2]


def _manifest(tmp_path: Path) -> PatchManifest:
    patch_file = tmp_path / "__data"
    patch_file.write_bytes(b"patch-data")
    transport = gzip_payload(patch_file, tmp_path / "addressables.bin.gz")
    return PatchManifest(
        patch=PatchMetadata(
            version="3.2.0-r1042.1",
            channel="release",
            route="INT_STEAM",
            build_id="build-1",
            translation_fingerprint="a" * 64,
        ),
        game=TargetGame(
            version="3.2.0",
            revision="1042",
            catalog_hash="b" * 32,
        ),
        files=(
            ManifestFile.from_paths(
                patch_file,
                transport.path,
                target="addressables",
                path="root/hash/__data",
                download_url="https://example.test/files/addressables.bin.gz",
            ),
        ),
    )


def test_manifest_is_valid_against_shared_schema(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    schema = json.loads((ROOT / "schemas/patch-manifest.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(manifest.as_dict())


def test_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    item = manifest.files[0]
    with pytest.raises(ValueError, match="safe"):
        ManifestFile(
            target=item.target,
            path="../escape",
            operation=item.operation,
            download_url=item.download_url,
            download_sha256=item.download_sha256,
            download_size=item.download_size,
            compression=item.compression,
            sha256=item.sha256,
            size=item.size,
        ).validate()


def test_release_index_upserts_exact_route_revision_channel() -> None:
    first = ReleaseIndexEntry(
        route="INT_STEAM",
        game_version="3.2.0",
        revision="1042",
        catalog_hash="b" * 32,
        channel="release",
        patch_version="v3.2.0-r1042-pre",
        manifest_url="https://example.test/pre/manifest.json",
        manifest_sha256="c" * 64,
    )
    second = ReleaseIndexEntry(
        route="INT_STEAM",
        game_version="3.2.0",
        revision="1042",
        catalog_hash="b" * 32,
        channel="release",
        patch_version="v3.2.0-r1042-release",
        manifest_url="https://example.test/release/manifest.json",
        manifest_sha256="d" * 64,
    )
    index = ReleaseIndex(()).upsert(first).upsert(second)
    assert len(index.releases) == 1
    assert index.releases[0].patch_version == "v3.2.0-r1042-release"
    assert ReleaseIndex.from_json(index.to_json()) == index

    schema = json.loads((ROOT / "schemas/release-index.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(index.as_dict())


def test_release_index_drops_legacy_channels_on_read() -> None:
    raw = json.dumps(
        {
            "schemaVersion": 1,
            "releases": [
                {
                    "route": "INT_STEAM",
                    "gameVersion": "3.2.0",
                    "revision": "1042",
                    "catalogHash": "b" * 32,
                    "channel": "stable",
                    "patchVersion": "legacy-stable",
                    "manifestUrl": "https://example.test/stable/manifest.json",
                    "manifestSha256": "c" * 64,
                },
                {
                    "route": "INT_STEAM",
                    "gameVersion": "3.2.0",
                    "revision": "1043",
                    "catalogHash": "d" * 32,
                    "channel": "preview",
                    "patchVersion": "legacy-preview",
                    "manifestUrl": "https://example.test/preview/manifest.json",
                    "manifestSha256": "e" * 64,
                },
            ],
        }
    )
    index = ReleaseIndex.from_json(raw)
    assert index.releases == ()


def test_manifest_digest_hashes_exact_bytes() -> None:
    expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert manifest_digest(b"abc") == expected
