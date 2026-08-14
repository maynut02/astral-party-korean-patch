from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Target = Literal["addressables", "game-data"]
Operation = Literal["create", "replace"]
Channel = Literal["release", "develop"]
Compression = Literal["gzip"]


def _hash_and_size(path: str | Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _validate_hex(value: str, field: str, length: int) -> None:
    if len(value) != length or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be lowercase {length}-character hex")


def _validate_sha256(value: str, field: str) -> None:
    _validate_hex(value, field, 64)


def _validate_catalog_hash(value: str) -> None:
    _validate_hex(value, "catalog_hash", 32)


@dataclass(frozen=True, slots=True)
class PatchMetadata:
    version: str
    channel: Channel
    route: str
    build_id: str
    translation_fingerprint: str

    def validate(self) -> None:
        if not all((self.version, self.route, self.build_id)):
            raise ValueError("patch version, route and build_id are required")
        if self.channel not in {"release", "develop"}:
            raise ValueError(f"unsupported patch channel: {self.channel}")
        _validate_sha256(self.translation_fingerprint, "translation_fingerprint")


@dataclass(frozen=True, slots=True)
class TargetGame:
    version: str
    revision: str
    catalog_hash: str

    def validate(self) -> None:
        if not self.version or not self.revision:
            raise ValueError("game version and revision are required")
        _validate_catalog_hash(self.catalog_hash)


@dataclass(frozen=True, slots=True)
class ManifestFile:
    target: Target
    path: str
    operation: Operation
    download_url: str
    download_sha256: str
    download_size: int
    compression: Compression
    sha256: str
    size: int
    source_sha256: str | None = None
    source_size: int | None = None

    @classmethod
    def from_paths(
        cls,
        payload: str | Path,
        transport: str | Path,
        *,
        target: Target,
        path: str,
        download_url: str,
        operation: Operation = "replace",
        compression: Compression = "gzip",
        source: str | Path | None = None,
    ) -> ManifestFile:
        payload_sha256, payload_size = _hash_and_size(payload)
        download_sha256, download_size = _hash_and_size(transport)
        source_sha256, source_size = _hash_and_size(source) if source is not None else (None, None)
        return cls(
            target=target,
            path=path.replace("\\", "/"),
            operation=operation,
            download_url=download_url,
            download_sha256=download_sha256,
            download_size=download_size,
            compression=compression,
            sha256=payload_sha256,
            size=payload_size,
            source_sha256=source_sha256,
            source_size=source_size,
        )

    def validate(self) -> None:
        if self.target not in {"addressables", "game-data"}:
            raise ValueError(f"unsupported manifest target: {self.target}")
        if self.operation not in {"create", "replace"}:
            raise ValueError(f"unsupported manifest operation: {self.operation}")
        normalized = self.path.replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError(f"manifest path must be safe and relative: {self.path!r}")
        if not self.download_url.startswith(("https://", "http://")):
            raise ValueError("manifest download_url must be an http(s) URL")
        if self.compression != "gzip":
            raise ValueError(f"unsupported transport compression: {self.compression}")
        _validate_sha256(self.download_sha256, "download sha256")
        _validate_sha256(self.sha256, "file sha256")
        if (self.source_sha256 is None) != (self.source_size is None):
            raise ValueError("manifest source sha256 and size must be provided together")
        if self.source_sha256 is not None:
            _validate_sha256(self.source_sha256, "source sha256")
            if self.source_size is None or self.source_size <= 0:
                raise ValueError("manifest source size must be positive")
        if self.download_size <= 0:
            raise ValueError("manifest download size must be positive")
        if self.size <= 0:
            raise ValueError("manifest file size must be positive")

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "target": self.target,
            "path": self.path,
            "operation": self.operation,
            "downloadUrl": self.download_url,
            "downloadSha256": self.download_sha256,
            "downloadSize": self.download_size,
            "compression": self.compression,
            "sha256": self.sha256,
            "size": self.size,
        }
        if self.source_sha256 is not None and self.source_size is not None:
            result["sourceSha256"] = self.source_sha256
            result["sourceSize"] = self.source_size
        return result


@dataclass(frozen=True, slots=True)
class PatchManifest:
    patch: PatchMetadata
    game: TargetGame
    files: tuple[ManifestFile, ...]
    schema_version: int = 2

    def validate(self) -> None:
        if self.schema_version != 2:
            raise ValueError(f"unsupported manifest schema: {self.schema_version}")
        self.patch.validate()
        self.game.validate()
        if not self.files:
            raise ValueError("manifest must contain at least one file")
        identities: set[tuple[str, str]] = set()
        for item in self.files:
            item.validate()
            identity = (item.target, item.path)
            if identity in identities:
                raise ValueError(f"duplicate manifest path: {identity}")
            identities.add(identity)

    def as_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schemaVersion": self.schema_version,
            "patch": {
                "version": self.patch.version,
                "channel": self.patch.channel,
                "route": self.patch.route,
                "buildId": self.patch.build_id,
                "translationFingerprint": self.patch.translation_fingerprint,
            },
            "game": {
                "version": self.game.version,
                "revision": self.game.revision,
                "catalogHash": self.game.catalog_hash,
            },
            "files": [item.as_dict() for item in self.files],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_manifest(manifest: PatchManifest, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        temp.write_text(manifest.to_json(), encoding="utf-8")
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    return output
