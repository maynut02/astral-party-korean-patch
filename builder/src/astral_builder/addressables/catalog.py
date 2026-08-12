from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ASSET_BUNDLE_RESOURCE = "UnityEngine.ResourceManagement.ResourceProviders.IAssetBundleResource"


class CatalogFormatError(ValueError):
    """Raised when a Unity Addressables JSON catalog cannot be decoded safely."""


@dataclass(frozen=True, slots=True)
class CatalogLocation:
    index: int
    internal_id: str
    provider_id: str
    dependency_key: object | None
    dependency_hash: int
    primary_key: object
    resource_type: str

    @property
    def is_asset_bundle(self) -> bool:
        return self.resource_type == _ASSET_BUNDLE_RESOURCE


@dataclass(frozen=True, slots=True)
class _Bucket:
    key_data_offset: int
    entry_indices: tuple[int, ...]


def _read_i32(data: bytes, offset: int) -> int:
    try:
        return struct.unpack_from("<i", data, offset)[0]
    except struct.error as exc:
        raise CatalogFormatError(f"cannot read int32 at offset {offset}") from exc


def _read_serialized_object(data: bytes, offset: int) -> object:
    """Read the key object format used by JsonContentCatalogData.

    Catalog keys only require primitive object forms for resolution. JsonObject is intentionally
    returned as a descriptive tuple instead of instantiating arbitrary Unity/.NET types.
    """
    if offset < 0 or offset >= len(data):
        raise CatalogFormatError(f"serialized object offset out of bounds: {offset}")

    object_type = data[offset]
    offset += 1

    if object_type in (0, 1):  # AsciiString / UnicodeString
        byte_length = _read_i32(data, offset)
        offset += 4
        end = offset + byte_length
        if byte_length < 0 or end > len(data):
            raise CatalogFormatError("serialized string exceeds key data bounds")
        encoding = "ascii" if object_type == 0 else "utf-16-le"
        return data[offset:end].decode(encoding)

    try:
        if object_type == 2:  # UInt16
            return struct.unpack_from("<H", data, offset)[0]
        if object_type == 3:  # UInt32
            return struct.unpack_from("<I", data, offset)[0]
        if object_type == 4:  # Int32
            return struct.unpack_from("<i", data, offset)[0]
    except struct.error as exc:
        raise CatalogFormatError("truncated numeric serialized object") from exc

    if object_type in (5, 6):  # Hash128 / Type
        if offset >= len(data):
            raise CatalogFormatError("truncated length-prefixed serialized object")
        byte_length = data[offset]
        offset += 1
        end = offset + byte_length
        if end > len(data):
            raise CatalogFormatError("serialized object exceeds key data bounds")
        return data[offset:end].decode("ascii", errors="strict")

    if object_type == 7:  # JsonObject
        if offset >= len(data):
            raise CatalogFormatError("truncated JsonObject")
        assembly_length = data[offset]
        offset += 1
        assembly = data[offset : offset + assembly_length].decode("ascii")
        offset += assembly_length
        if offset >= len(data):
            raise CatalogFormatError("truncated JsonObject class name")
        class_length = data[offset]
        offset += 1
        class_name = data[offset : offset + class_length].decode("ascii")
        offset += class_length
        json_length = _read_i32(data, offset)
        offset += 4
        end = offset + json_length
        if json_length < 0 or end > len(data):
            raise CatalogFormatError("JsonObject payload exceeds bounds")
        payload = data[offset:end].decode("utf-16-le")
        return ("json", assembly, class_name, payload)

    raise CatalogFormatError(f"unsupported serialized object type: {object_type}")


def _resource_type_name(value: Any) -> str:
    if not isinstance(value, dict):
        raise CatalogFormatError("resource type entry must be an object")
    class_name = value.get("m_ClassName")
    if not isinstance(class_name, str):
        raise CatalogFormatError("resource type entry has no m_ClassName")
    return class_name


class AddressablesCatalog:
    """Decoded view of Unity's JSON Addressables content catalog."""

    def __init__(
        self,
        *,
        locator_id: str,
        build_result_hash: str | None,
        keys: tuple[object, ...],
        buckets: tuple[_Bucket, ...],
        locations: tuple[CatalogLocation, ...],
    ) -> None:
        self.locator_id = locator_id
        self.build_result_hash = build_result_hash
        self.keys = keys
        self._buckets = buckets
        self.locations = locations
        self._key_to_index = {key: index for index, key in enumerate(keys) if _is_hashable(key)}

    @classmethod
    def from_path(cls, path: str | Path) -> AddressablesCatalog:
        raw = Path(path).read_text(encoding="utf-8-sig")
        return cls.from_json(raw)

    @classmethod
    def from_json(cls, raw: str | bytes) -> AddressablesCatalog:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CatalogFormatError("invalid catalog JSON") from exc
        if not isinstance(data, dict):
            raise CatalogFormatError("catalog root must be an object")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AddressablesCatalog:
        try:
            bucket_data = base64.b64decode(data["m_BucketDataString"], validate=True)
            key_data = base64.b64decode(data["m_KeyDataString"], validate=True)
            entry_data = base64.b64decode(data["m_EntryDataString"], validate=True)
            internal_ids = data["m_InternalIds"]
            provider_ids = data["m_ProviderIds"]
            resource_types = data["m_resourceTypes"]
        except (KeyError, TypeError, ValueError) as exc:
            raise CatalogFormatError("catalog is missing required encoded data") from exc

        buckets = _decode_buckets(bucket_data)
        key_count = _read_i32(key_data, 0)
        if key_count != len(buckets):
            raise CatalogFormatError(
                f"key count ({key_count}) does not match bucket count ({len(buckets)})"
            )
        keys = tuple(_read_serialized_object(key_data, bucket.key_data_offset) for bucket in buckets)
        locations = _decode_locations(
            entry_data=entry_data,
            keys=keys,
            internal_ids=internal_ids,
            provider_ids=provider_ids,
            resource_types=resource_types,
        )

        for bucket in buckets:
            for entry_index in bucket.entry_indices:
                if entry_index < 0 or entry_index >= len(locations):
                    raise CatalogFormatError(f"bucket references invalid entry {entry_index}")

        locator_id = data.get("m_LocatorId", "")
        if not isinstance(locator_id, str):
            raise CatalogFormatError("m_LocatorId must be a string")
        build_hash = data.get("m_BuildResultHash")
        if build_hash is not None and not isinstance(build_hash, str):
            raise CatalogFormatError("m_BuildResultHash must be a string or null")
        return cls(
            locator_id=locator_id,
            build_result_hash=build_hash,
            keys=keys,
            buckets=buckets,
            locations=locations,
        )

    def locate(self, key: object) -> tuple[CatalogLocation, ...]:
        index = self._key_to_index.get(key)
        if index is None:
            return ()
        return tuple(self.locations[i] for i in self._buckets[index].entry_indices)

    def dependency_locations(self, location: CatalogLocation) -> tuple[CatalogLocation, ...]:
        if location.dependency_key is None:
            return ()
        return self.locate(location.dependency_key)

    def bundle_dependencies(self, key: object, *, recursive: bool = True) -> tuple[CatalogLocation, ...]:
        """Return unique AssetBundle locations needed to load a catalog key."""
        result: list[CatalogLocation] = []
        seen_locations: set[int] = set()
        seen_keys: set[object] = set()

        def visit_key(current_key: object) -> None:
            if _is_hashable(current_key):
                if current_key in seen_keys:
                    return
                seen_keys.add(current_key)
            for location in self.locate(current_key):
                if location.is_asset_bundle and location.index not in seen_locations:
                    seen_locations.add(location.index)
                    result.append(location)
                if recursive and location.dependency_key is not None:
                    visit_key(location.dependency_key)

        for location in self.locate(key):
            if location.is_asset_bundle and location.index not in seen_locations:
                seen_locations.add(location.index)
                result.append(location)
            if location.dependency_key is not None:
                visit_key(location.dependency_key)

        return tuple(result)


def _is_hashable(value: object) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


def _decode_buckets(data: bytes) -> tuple[_Bucket, ...]:
    count = _read_i32(data, 0)
    if count < 0:
        raise CatalogFormatError("negative bucket count")
    offset = 4
    buckets: list[_Bucket] = []
    for _ in range(count):
        key_data_offset = _read_i32(data, offset)
        entry_count = _read_i32(data, offset + 4)
        offset += 8
        if entry_count < 0:
            raise CatalogFormatError("negative bucket entry count")
        indices = tuple(_read_i32(data, offset + i * 4) for i in range(entry_count))
        offset += entry_count * 4
        buckets.append(_Bucket(key_data_offset, indices))
    if offset != len(data):
        raise CatalogFormatError("unexpected trailing bucket data")
    return tuple(buckets)


def _decode_locations(
    *,
    entry_data: bytes,
    keys: tuple[object, ...],
    internal_ids: Any,
    provider_ids: Any,
    resource_types: Any,
) -> tuple[CatalogLocation, ...]:
    if not isinstance(internal_ids, list) or not isinstance(provider_ids, list):
        raise CatalogFormatError("internal/provider id tables must be arrays")
    if not isinstance(resource_types, list):
        raise CatalogFormatError("resource type table must be an array")

    count = _read_i32(entry_data, 0)
    if count < 0 or len(entry_data) != 4 + count * 28:
        raise CatalogFormatError("entry data length does not match the 7-int record format")

    result: list[CatalogLocation] = []
    for index in range(count):
        offset = 4 + index * 28
        internal_id_index, provider_index, dependency_index, dependency_hash, _, primary_key_index, type_index = struct.unpack_from(
            "<7i", entry_data, offset
        )
        try:
            internal_id = internal_ids[internal_id_index]
            provider_id = provider_ids[provider_index]
            primary_key = keys[primary_key_index]
            resource_type = _resource_type_name(resource_types[type_index])
            dependency_key = None if dependency_index < 0 else keys[dependency_index]
        except (IndexError, TypeError) as exc:
            raise CatalogFormatError(f"entry {index} references an invalid table index") from exc
        if not isinstance(internal_id, str) or not isinstance(provider_id, str):
            raise CatalogFormatError("internal/provider ids must be strings")
        result.append(
            CatalogLocation(
                index=index,
                internal_id=internal_id,
                provider_id=provider_id,
                dependency_key=dependency_key,
                dependency_hash=dependency_hash,
                primary_key=primary_key,
                resource_type=resource_type,
            )
        )
    return tuple(result)
