from __future__ import annotations

import hashlib
import io
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import UnityPy
from PIL import Image


class FontPatchError(RuntimeError):
    """Raised when a font patch target cannot be replaced or verified safely."""


Loader = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class FontPatchResult:
    output_path: Path
    target_name: str
    sha256: str
    size: int


def _save_environment_atomic(environment: Any, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        temp.write_bytes(environment.file.save())
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    return output


def _asset_name(obj: Any) -> tuple[Any, str]:
    asset = obj.read()
    return asset, str(getattr(asset, "m_Name", "") or "").strip()


def patch_tmp_font_bundle(
    input_path: str | Path,
    output_path: str | Path,
    *,
    mono_name: str,
    mono_payload: bytes,
    texture_name: str,
    atlas_png: bytes,
    loader: Loader = UnityPy.load,
) -> FontPatchResult:
    environment = loader(str(input_path))
    mono_found = False
    texture_found = False
    atlas_image = Image.open(io.BytesIO(atlas_png)).convert("RGBA")

    for obj in environment.objects:
        type_name = getattr(getattr(obj, "type", None), "name", "")
        if type_name == "MonoBehaviour":
            try:
                _, name = _asset_name(obj)
            except Exception:
                continue
            if name == mono_name:
                if mono_found:
                    raise FontPatchError(f"duplicate MonoBehaviour target: {mono_name}")
                obj.set_raw_data(mono_payload)
                mono_found = True
        elif type_name == "Texture2D":
            asset, name = _asset_name(obj)
            if name == texture_name:
                if texture_found:
                    raise FontPatchError(f"duplicate Texture2D target: {texture_name}")
                if hasattr(asset, "set_image"):
                    asset.set_image(atlas_image.copy())
                else:
                    asset.image = atlas_image.copy()
                asset.save()
                texture_found = True

    if not mono_found:
        raise FontPatchError(f"MonoBehaviour target not found: {mono_name}")
    if not texture_found:
        raise FontPatchError(f"Texture2D target not found: {texture_name}")

    output = _save_environment_atomic(environment, output_path)
    _verify_tmp_font_bundle(
        output,
        mono_name=mono_name,
        mono_payload=mono_payload,
        texture_name=texture_name,
        atlas_image=atlas_image,
        loader=loader,
    )
    data = output.read_bytes()
    return FontPatchResult(output, mono_name, hashlib.sha256(data).hexdigest(), len(data))


def _verify_tmp_font_bundle(
    path: Path,
    *,
    mono_name: str,
    mono_payload: bytes,
    texture_name: str,
    atlas_image: Image.Image,
    loader: Loader,
) -> None:
    environment = loader(str(path))
    mono_ok = False
    texture_ok = False
    expected_pixels = hashlib.sha256(atlas_image.tobytes()).digest()

    for obj in environment.objects:
        type_name = getattr(getattr(obj, "type", None), "name", "")
        if type_name == "MonoBehaviour":
            try:
                _, name = _asset_name(obj)
            except Exception:
                continue
            if name == mono_name:
                mono_ok = obj.get_raw_data() == mono_payload
        elif type_name == "Texture2D":
            asset, name = _asset_name(obj)
            if name == texture_name:
                image = asset.image.convert("RGBA")
                actual_pixels = hashlib.sha256(image.tobytes()).digest()
                texture_ok = image.size == atlas_image.size and actual_pixels == expected_pixels

    if not mono_ok:
        raise FontPatchError(f"MonoBehaviour verification failed: {mono_name}")
    if not texture_ok:
        raise FontPatchError(f"Texture2D verification failed: {texture_name}")


def patch_legacy_font(
    input_path: str | Path,
    output_path: str | Path,
    *,
    font_name: str,
    font_payload: bytes,
    loader: Loader = UnityPy.load,
) -> FontPatchResult:
    environment = loader(str(input_path))
    found = False

    for obj in environment.objects:
        if getattr(getattr(obj, "type", None), "name", "") != "Font":
            continue
        asset, name = _asset_name(obj)
        if name != font_name:
            continue
        if found:
            raise FontPatchError(f"duplicate Font target: {font_name}")
        current = getattr(asset, "m_FontData", None)
        if isinstance(current, list):
            asset.m_FontData = list(font_payload)
        else:
            asset.m_FontData = font_payload
        asset.save()
        found = True

    if not found:
        raise FontPatchError(f"Font target not found: {font_name}")

    output = _save_environment_atomic(environment, output_path)
    _verify_legacy_font(output, font_name=font_name, font_payload=font_payload, loader=loader)
    data = output.read_bytes()
    return FontPatchResult(output, font_name, hashlib.sha256(data).hexdigest(), len(data))


def _verify_legacy_font(
    path: Path,
    *,
    font_name: str,
    font_payload: bytes,
    loader: Loader,
) -> None:
    environment = loader(str(path))
    for obj in environment.objects:
        if getattr(getattr(obj, "type", None), "name", "") != "Font":
            continue
        asset, name = _asset_name(obj)
        if name != font_name:
            continue
        raw = getattr(asset, "m_FontData", b"")
        actual = bytes(raw) if not isinstance(raw, bytes) else raw
        if actual == font_payload:
            return
        raise FontPatchError(f"Font verification failed: {font_name}")
    raise FontPatchError(f"Font disappeared after reload: {font_name}")
