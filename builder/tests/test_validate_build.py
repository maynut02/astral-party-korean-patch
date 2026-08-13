import gzip
import hashlib
from pathlib import Path

import pytest

from astral_builder.automation.validate_build import (
    _asset_file,
    _extract_payload,
    _verify_transport,
)
from astral_builder.validate.assets import ValidationError


def test_asset_file_uses_download_url_basename(tmp_path: Path) -> None:
    asset = tmp_path / "lang-test.bin.gz"
    asset.write_bytes(b"x")
    assert _asset_file(tmp_path, "https://example.test/release/lang-test.bin.gz") == asset


def test_asset_file_rejects_missing_release_asset(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="not found"):
        _asset_file(tmp_path, "https://example.test/release/missing.bin.gz")


def test_transport_and_extracted_payload_are_both_verified(tmp_path: Path) -> None:
    payload = b"patched-unity-data" * 1024
    transport = tmp_path / "payload.gz"
    transport.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
    item = {
        "downloadSize": transport.stat().st_size,
        "downloadSha256": hashlib.sha256(transport.read_bytes()).hexdigest(),
        "compression": "gzip",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

    _verify_transport(transport, item)
    extracted = tmp_path / "payload"
    _extract_payload(transport, extracted, item)
    assert extracted.read_bytes() == payload


def test_payload_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    payload = b"patched-unity-data"
    transport = tmp_path / "payload.gz"
    transport.write_bytes(gzip.compress(payload, mtime=0))
    item = {
        "downloadSize": transport.stat().st_size,
        "downloadSha256": hashlib.sha256(transport.read_bytes()).hexdigest(),
        "compression": "gzip",
        "size": len(payload),
        "sha256": "0" * 64,
    }

    with pytest.raises(ValidationError, match="payload sha256 mismatch"):
        _extract_payload(transport, tmp_path / "payload", item)
