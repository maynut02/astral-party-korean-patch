from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from astral_builder.release.index import ReleaseIndex, ReleaseIndexEntry, manifest_digest


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    build_id: str
    patch_version: str
    channel: str
    route: str
    game_version: str
    revision: str
    catalog_hash: str
    addressables_paths: tuple[str, ...]


def read_release_metadata(manifest_path: str | Path) -> ReleaseMetadata:
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 2:
        raise ValueError("unsupported patch manifest schema")
    patch = data.get("patch")
    game = data.get("game")
    if not isinstance(patch, dict) or not isinstance(game, dict):
        raise ValueError("manifest is missing patch/game metadata")
    files = data.get("files")
    if not isinstance(files, list):
        raise ValueError("manifest is missing files")
    addressables_paths = tuple(
        sorted(
            {
                str(item["path"]).replace("\\", "/")
                for item in files
                if isinstance(item, dict) and item.get("target") == "addressables"
            }
        )
    )
    metadata = ReleaseMetadata(
        build_id=str(patch["buildId"]),
        patch_version=str(patch["version"]),
        channel=str(patch["channel"]),
        route=str(patch["route"]),
        game_version=str(game["version"]),
        revision=str(game["revision"]),
        catalog_hash=str(game["catalogHash"]),
        addressables_paths=addressables_paths,
    )
    if metadata.channel not in {"release", "develop"}:
        raise ValueError(f"unsupported release channel: {metadata.channel}")
    return metadata


def update_release_index(
    *,
    manifest_path: str | Path,
    manifest_url: str,
    index_path: str | Path,
) -> ReleaseIndex:
    manifest_path = Path(manifest_path)
    metadata = read_release_metadata(manifest_path)
    index_file = Path(index_path)
    if index_file.is_file():
        index = ReleaseIndex.from_json(index_file.read_text(encoding="utf-8"))
    else:
        index = ReleaseIndex(())
    entry = ReleaseIndexEntry(
        route=metadata.route,
        game_version=metadata.game_version,
        revision=metadata.revision,
        catalog_hash=metadata.catalog_hash,
        channel=metadata.channel,
        patch_version=metadata.patch_version,
        manifest_url=manifest_url,
        manifest_sha256=manifest_digest(manifest_path.read_bytes()),
        addressables_paths=(metadata.addressables_paths if metadata.route == "INT_ANDROID" else ()),
    )
    updated = index.upsert(entry)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text(updated.to_json(), encoding="utf-8")
    return updated


def write_release_github_output(metadata: ReleaseMetadata, destination: str | Path) -> None:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(f"build_id={metadata.build_id}\n")
        file.write(f"patch_version={metadata.patch_version}\n")
        file.write(f"channel={metadata.channel}\n")
        file.write(f"route={metadata.route}\n")
        file.write(f"game_version={metadata.game_version}\n")
        file.write(f"revision={metadata.revision}\n")
        file.write(f"catalog_hash={metadata.catalog_hash}\n")
