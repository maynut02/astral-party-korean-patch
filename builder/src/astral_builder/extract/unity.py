from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import UnityPy


class UnityExtractionError(RuntimeError):
    """Raised when expected Unity objects cannot be extracted safely."""


Loader = Callable[[str], Any]


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8", "surrogateescape")
    try:
        return bytes(value)
    except (TypeError, ValueError) as exc:
        raise UnityExtractionError(f"unsupported TextAsset script type: {type(value)!r}") from exc


def extract_object_names(
    bundle_path: str | Path,
    *,
    type_name: str,
    loader: Loader = UnityPy.load,
) -> tuple[str, ...]:
    """Return non-empty Unity object names for one serialized type."""
    environment = loader(str(bundle_path))
    result: list[str] = []
    for obj in environment.objects:
        if getattr(getattr(obj, "type", None), "name", "") != type_name:
            continue
        try:
            asset = obj.read()
        except Exception:
            continue
        name = str(getattr(asset, "m_Name", "") or "").strip()
        if name:
            result.append(name)
    return tuple(result)


def extract_text_assets(
    bundle_path: str | Path,
    *,
    names: Iterable[str] | None = None,
    name_prefix: str | None = None,
    loader: Loader = UnityPy.load,
) -> dict[str, bytes]:
    """Extract selected TextAsset payloads from one Unity bundle without any DB concerns."""
    requested = None if names is None else set(names)
    environment = loader(str(bundle_path))
    result: dict[str, bytes] = {}

    for obj in environment.objects:
        if getattr(getattr(obj, "type", None), "name", "") != "TextAsset":
            continue
        asset = obj.read()
        asset_name = str(getattr(asset, "m_Name", "") or "").strip()
        if not asset_name:
            continue
        if requested is not None and asset_name not in requested:
            continue
        if name_prefix is not None and not asset_name.startswith(name_prefix):
            continue
        if asset_name in result:
            raise UnityExtractionError(f"duplicate TextAsset name in bundle: {asset_name}")
        result[asset_name] = _as_bytes(getattr(asset, "m_Script", b""))

    if requested is not None:
        missing = sorted(requested - set(result))
        if missing:
            raise UnityExtractionError(f"requested TextAssets were not found: {missing}")
    return result
