from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import UnityPy
from PIL import Image

from astral_builder.formats.astral_str import decode_str
from astral_builder.formats.lang_xml import decode_lang_xml


class ValidationError(RuntimeError):
    """Raised when a built patch output fails an invariant."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    name: str
    details: str


Loader = Callable[[str], Any]


def validate_file(path: str | Path) -> ValidationResult:
    file_path = Path(path)
    if not file_path.is_file():
        raise ValidationError(f"output file does not exist: {file_path}")
    size = file_path.stat().st_size
    if size <= 0:
        raise ValidationError(f"output file is empty: {file_path}")
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return ValidationResult("file", f"size={size} sha256={digest}")


def validate_lang_payload(original: bytes | str, patched: bytes | str) -> ValidationResult:
    before = decode_lang_xml(original)
    after = decode_lang_xml(patched)
    if tuple(before) != tuple(after):
        raise ValidationError("Lang key order/set changed during patching")
    return ValidationResult("lang", f"keys={len(before)}")


def validate_str_payload(original: bytes, patched: bytes) -> ValidationResult:
    if not original and not patched:
        return ValidationResult("str", "entries=0")
    before = decode_str(original)
    after = decode_str(patched)
    before_ids = tuple(entry.id for entry in before.entries)
    after_ids = tuple(entry.id for entry in after.entries)
    if before_ids != after_ids:
        raise ValidationError("STR id order/set changed during patching")
    if before.paired != after.paired:
        raise ValidationError("STR paired representation changed during patching")
    return ValidationResult("str", f"entries={len(before_ids)} paired={before.paired}")


def _assetbundle_name(path: str | Path, loader: Loader) -> str | None:
    environment = loader(str(path))
    names: list[str] = []
    for obj in environment.objects:
        if getattr(getattr(obj, "type", None), "name", "") != "AssetBundle":
            continue
        asset = obj.read()
        names.append(str(getattr(asset, "m_Name", "") or ""))
    if not names:
        return None
    if len(names) != 1:
        raise ValidationError(f"expected one AssetBundle object, found {len(names)}")
    return names[0]


def validate_assetbundle_name(
    original_path: str | Path,
    patched_path: str | Path,
    *,
    loader: Loader = UnityPy.load,
) -> ValidationResult:
    before = _assetbundle_name(original_path, loader)
    after = _assetbundle_name(patched_path, loader)
    if before != after:
        raise ValidationError(f"AssetBundle m_Name changed: before={before!r} after={after!r}")
    return ValidationResult("assetbundle", f"m_Name={before!r}")


def validate_tmp_font(
    bundle_path: str | Path,
    *,
    mono_name: str,
    mono_payload: bytes,
    texture_name: str,
    atlas_png: bytes,
    loader: Loader = UnityPy.load,
) -> ValidationResult:
    environment = loader(str(bundle_path))
    expected_image = Image.open(io.BytesIO(atlas_png)).convert("RGBA")
    expected_hash = hashlib.sha256(expected_image.tobytes()).digest()
    mono_ok = False
    texture_ok = False

    for obj in environment.objects:
        type_name = getattr(getattr(obj, "type", None), "name", "")
        try:
            asset = obj.read()
        except Exception:
            continue
        name = str(getattr(asset, "m_Name", "") or "")
        if type_name == "MonoBehaviour" and name == mono_name:
            mono_ok = obj.get_raw_data() == mono_payload
        if type_name == "Texture2D" and name == texture_name:
            image = asset.image.convert("RGBA")
            texture_ok = (
                image.size == expected_image.size
                and hashlib.sha256(image.tobytes()).digest() == expected_hash
            )

    if not mono_ok:
        raise ValidationError(f"TMP MonoBehaviour mismatch: {mono_name}")
    if not texture_ok:
        raise ValidationError(f"TMP atlas mismatch: {texture_name}")
    return ValidationResult("tmp-font", f"mono={mono_name} texture={texture_name}")


def validate_legacy_font(
    data_path: str | Path,
    *,
    font_name: str,
    font_payload: bytes,
    loader: Loader = UnityPy.load,
) -> ValidationResult:
    environment = loader(str(data_path))
    for obj in environment.objects:
        if getattr(getattr(obj, "type", None), "name", "") != "Font":
            continue
        asset = obj.read()
        if str(getattr(asset, "m_Name", "") or "") != font_name:
            continue
        raw = getattr(asset, "m_FontData", b"")
        actual = raw if isinstance(raw, bytes) else bytes(raw)
        if actual != font_payload:
            raise ValidationError(f"legacy font payload mismatch: {font_name}")
        return ValidationResult("legacy-font", f"font={font_name} bytes={len(actual)}")
    raise ValidationError(f"legacy font target not found: {font_name}")
