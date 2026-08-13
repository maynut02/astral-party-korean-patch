from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from astral_builder.automation.sync import load_route_sync_config
from astral_builder.extract.unity import extract_text_assets
from astral_builder.formats.astral_str import decode_str
from astral_builder.formats.lang_xml import decode_lang_xml
from astral_builder.validate.assets import (
    ValidationError,
    validate_assetbundle_expected_name,
    validate_file,
    validate_legacy_font,
    validate_tmp_font,
)


@dataclass(frozen=True, slots=True)
class BuildValidationSummary:
    file_count: int
    lang_keys: int
    str_assets: int
    str_units: int


def _asset_file(assets_dir: Path, download_url: str) -> Path:
    name = Path(urllib.parse.urlparse(download_url).path).name
    if not name:
        raise ValidationError(f"manifest downloadUrl has no filename: {download_url}")
    path = assets_dir / name
    if not path.is_file():
        raise ValidationError(f"release asset file not found: {path}")
    return path


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _verify_transport(path: Path, item: dict[str, object]) -> None:
    expected_size = int(item["downloadSize"])
    expected_sha = str(item["downloadSha256"])
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise ValidationError(
            f"download size mismatch for {path.name}: expected={expected_size} actual={actual_size}"
        )
    actual_sha = _sha256(path)
    if actual_sha != expected_sha:
        raise ValidationError(
            f"download sha256 mismatch for {path.name}: expected={expected_sha} actual={actual_sha}"
        )
    if item.get("compression") != "gzip":
        raise ValidationError(f"unsupported transport compression: {item.get('compression')}")


def _extract_payload(transport: Path, destination: Path, item: dict[str, object]) -> None:
    expected_size = int(item["size"])
    hasher = hashlib.sha256()
    total = 0
    try:
        with gzip.open(transport, "rb") as source, destination.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > expected_size:
                    raise ValidationError(
                        f"decompressed payload exceeds manifest size for {transport.name}"
                    )
                hasher.update(chunk)
                output.write(chunk)
    except OSError as exc:
        raise ValidationError(f"invalid gzip transport {transport.name}: {exc}") from exc

    if total != expected_size:
        raise ValidationError(
            f"payload size mismatch for {transport.name}: expected={expected_size} actual={total}"
        )
    actual_sha = hasher.hexdigest()
    expected_sha = str(item["sha256"])
    if actual_sha != expected_sha:
        raise ValidationError(
            f"payload sha256 mismatch for {transport.name}: "
            f"expected={expected_sha} actual={actual_sha}"
        )


def validate_built_patch(
    manifest_path: str | Path,
    assets_dir: str | Path,
    *,
    route_config: str | Path,
    resources_root: str | Path = "resources/int_steam",
) -> BuildValidationSummary:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 2:
        raise ValidationError("unsupported patch manifest schema")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValidationError("patch manifest has no files")

    config = load_route_sync_config(route_config)
    root = Path(assets_dir)
    resources = Path(resources_root)
    lang_keys = 0
    str_assets = 0
    str_units = 0
    saw_lang = saw_str = saw_tmp = saw_legacy = False

    with tempfile.TemporaryDirectory(prefix="astral-validate-") as temp_dir:
        temp_root = Path(temp_dir)
        for index, raw in enumerate(files):
            if not isinstance(raw, dict):
                raise ValidationError("manifest file entry is not an object")
            item = raw
            transport = _asset_file(root, str(item["downloadUrl"]))
            _verify_transport(transport, item)
            payload = temp_root / f"payload-{index}"
            _extract_payload(transport, payload, item)
            validate_file(payload)

            target = str(item["target"])
            relative = str(item["path"]).replace("\\", "/")

            if target == "addressables":
                parts = relative.split("/")
                if len(parts) != 3 or parts[-1] != "__data":
                    raise ValidationError(f"invalid Addressables cache path: {relative}")
                validate_assetbundle_expected_name(payload, f"{parts[0]}.bundle")
                text_assets = extract_text_assets(payload)
                english = config.lang_assets["en"]
                if english in text_assets:
                    lang_keys = len(decode_lang_xml(text_assets[english]))
                    saw_lang = True
                matching_str = {
                    name: data
                    for name, data in text_assets.items()
                    if name.startswith(config.str_asset_prefix)
                }
                if matching_str:
                    str_assets = len(matching_str)
                    for data in matching_str.values():
                        if data:
                            str_units += len(decode_str(data).entries)
                    saw_str = True
                if any(name == config.tmp_asset_name for name in text_assets):
                    raise ValidationError("TMP font target unexpectedly serialized as TextAsset")
                if "tmp-font-" in transport.name:
                    validate_tmp_font(
                        payload,
                        mono_name=config.tmp_asset_name,
                        mono_payload=(resources / "tmp-font.dat").read_bytes(),
                        texture_name=f"{config.tmp_asset_name} Atlas",
                        atlas_png=(resources / "tmp-font-atlas.png").read_bytes(),
                    )
                    saw_tmp = True
            elif target == "game-data":
                if relative != "data.unity3d":
                    raise ValidationError(f"unexpected game-data path: {relative}")
                validate_legacy_font(
                    payload,
                    font_name="Afacad-Regular",
                    font_payload=(resources / "legacy-font.ttf").read_bytes(),
                )
                saw_legacy = True
            else:
                raise ValidationError(f"unsupported manifest target: {target}")

    missing = [
        name
        for name, present in (
            ("lang", saw_lang),
            ("str", saw_str),
            ("tmp-font", saw_tmp),
            ("legacy-font", saw_legacy),
        )
        if not present
    ]
    if missing:
        raise ValidationError(f"complete patch is missing validated targets: {missing}")
    return BuildValidationSummary(len(files), lang_keys, str_assets, str_units)
