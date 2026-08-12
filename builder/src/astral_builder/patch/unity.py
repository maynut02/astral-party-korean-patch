from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import UnityPy


class UnityPatchError(RuntimeError):
    """Raised when a Unity bundle cannot be patched or verified safely."""


Loader = Callable[[str], Any]


def _payload_to_script(payload: bytes) -> str:
    return payload.decode("utf-8", "surrogateescape")


def _script_to_bytes(value: Any) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8", "surrogateescape")
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    try:
        return bytes(value)
    except (TypeError, ValueError) as exc:
        raise UnityPatchError(f"unsupported TextAsset script type: {type(value)!r}") from exc


def patch_text_assets(
    input_path: str | Path,
    output_path: str | Path,
    replacements: Mapping[str, bytes],
    *,
    loader: Loader = UnityPy.load,
    verify: bool = True,
) -> tuple[str, ...]:
    """Replace named TextAssets and atomically save a Unity bundle."""
    if not replacements:
        raise ValueError("at least one TextAsset replacement is required")

    environment = loader(str(input_path))
    found: set[str] = set()
    for obj in environment.objects:
        if getattr(getattr(obj, "type", None), "name", "") != "TextAsset":
            continue
        asset = obj.read()
        name = str(getattr(asset, "m_Name", "") or "").strip()
        payload = replacements.get(name)
        if payload is None:
            continue
        if name in found:
            raise UnityPatchError(f"duplicate TextAsset target in bundle: {name}")
        asset.m_Script = _payload_to_script(payload)
        asset.save()
        found.add(name)

    missing = sorted(set(replacements) - found)
    if missing:
        raise UnityPatchError(f"TextAsset patch targets were not found: {missing}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        temp_path.write_bytes(environment.file.save())
        if verify:
            _verify_text_assets(temp_path, replacements, loader=loader)
        temp_path.replace(output)
    finally:
        temp_path.unlink(missing_ok=True)

    return tuple(sorted(found))


def _verify_text_assets(
    bundle_path: Path,
    replacements: Mapping[str, bytes],
    *,
    loader: Loader,
) -> None:
    environment = loader(str(bundle_path))
    actual: dict[str, bytes] = {}
    for obj in environment.objects:
        if getattr(getattr(obj, "type", None), "name", "") != "TextAsset":
            continue
        asset = obj.read()
        name = str(getattr(asset, "m_Name", "") or "").strip()
        if name in replacements:
            actual[name] = _script_to_bytes(getattr(asset, "m_Script", b""))

    for name, expected in replacements.items():
        if name not in actual:
            raise UnityPatchError(f"patched TextAsset disappeared after reload: {name}")
        if actual[name] != expected:
            raise UnityPatchError(f"patched TextAsset changed after reload: {name}")
