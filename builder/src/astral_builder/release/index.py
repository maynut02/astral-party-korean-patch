from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

Channel = Literal["release"]


@dataclass(frozen=True, slots=True)
class ReleaseIndexEntry:
    route: str
    game_version: str
    revision: str
    catalog_hash: str
    channel: Channel
    patch_version: str
    manifest_url: str
    manifest_sha256: str
    addressables_paths: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.route, self.game_version, self.revision)

    def validate(self) -> None:
        if self.channel != "release":
            raise ValueError(f"unsupported release channel: {self.channel}")
        if len(self.catalog_hash) != 32 or any(
            char not in "0123456789abcdef" for char in self.catalog_hash
        ):
            raise ValueError("catalog_hash must be lowercase 32-character hex")
        if len(self.manifest_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.manifest_sha256
        ):
            raise ValueError("manifest_sha256 must be lowercase SHA-256 hex")
        if not all(
            (
                self.route,
                self.game_version,
                self.revision,
                self.patch_version,
                self.manifest_url,
            )
        ):
            raise ValueError("release index entry contains an empty required field")
        if len(set(self.addressables_paths)) != len(self.addressables_paths):
            raise ValueError("release index addressables paths must be unique")
        for path in self.addressables_paths:
            normalized = path.replace("\\", "/")
            if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError(f"unsafe release index addressables path: {path!r}")

    def as_dict(self) -> dict[str, object]:
        self.validate()
        result: dict[str, object] = {
            "route": self.route,
            "gameVersion": self.game_version,
            "revision": self.revision,
            "catalogHash": self.catalog_hash,
            "channel": self.channel,
            "patchVersion": self.patch_version,
            "manifestUrl": self.manifest_url,
            "manifestSha256": self.manifest_sha256,
        }
        if self.addressables_paths:
            result["addressablesPaths"] = list(self.addressables_paths)
        return result


@dataclass(frozen=True, slots=True)
class ReleaseIndex:
    releases: tuple[ReleaseIndexEntry, ...]
    schema_version: int = 1

    def upsert(self, entry: ReleaseIndexEntry) -> ReleaseIndex:
        entry.validate()
        by_identity = {item.identity: item for item in self.releases}
        by_identity[entry.identity] = entry
        ordered = tuple(sorted(by_identity.values(), key=lambda item: item.identity))
        return ReleaseIndex(ordered, self.schema_version)

    def as_dict(self) -> dict[str, object]:
        if self.schema_version != 1:
            raise ValueError(f"unsupported release index schema: {self.schema_version}")
        return {
            "schemaVersion": self.schema_version,
            "releases": [entry.as_dict() for entry in self.releases],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, raw: str | bytes) -> ReleaseIndex:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        releases = tuple(
            ReleaseIndexEntry(
                route=item["route"],
                game_version=item["gameVersion"],
                revision=item["revision"],
                catalog_hash=item["catalogHash"],
                channel="release",
                patch_version=item["patchVersion"],
                manifest_url=item["manifestUrl"],
                manifest_sha256=item["manifestSha256"],
                addressables_paths=tuple(item.get("addressablesPaths", ())),
            )
            for item in data.get("releases", [])
            if item.get("channel") == "release"
        )
        index = cls(releases, int(data.get("schemaVersion", 0)))
        index.as_dict()
        return index


def manifest_digest(raw: str | bytes) -> str:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
