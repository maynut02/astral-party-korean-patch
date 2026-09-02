from astral_builder.patch.builder import BundlePatchResult, patch_lang_bundle, patch_str_bundle
from astral_builder.patch.fonts import (
    FontPatchError,
    FontPatchResult,
    patch_legacy_font,
    patch_tmp_font_bundle,
)
from astral_builder.patch.translations import (
    PatchStats,
    patch_lang_payload,
    patch_str_payload,
    select_translation,
)

__all__ = [
    "FontPatchError",
    "FontPatchResult",
    "BundlePatchResult",
    "PatchStats",
    "UnityPatchError",
    "patch_lang_bundle",
    "patch_legacy_font",
    "patch_lang_payload",
    "patch_str_bundle",
    "patch_str_payload",
    "patch_text_assets",
    "patch_tmp_font_bundle",
    "select_translation",
]

from astral_builder.patch.unity import UnityPatchError, patch_text_assets
