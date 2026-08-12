from pathlib import Path

import pytest

from astral_builder.automation.validate_build import _asset_file
from astral_builder.validate.assets import ValidationError


def test_asset_file_uses_download_url_basename(tmp_path: Path) -> None:
    asset = tmp_path / "lang-test.bin"
    asset.write_bytes(b"x")
    assert _asset_file(tmp_path, "https://example.test/release/lang-test.bin") == asset


def test_asset_file_rejects_missing_release_asset(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="not found"):
        _asset_file(tmp_path, "https://example.test/release/missing.bin")
