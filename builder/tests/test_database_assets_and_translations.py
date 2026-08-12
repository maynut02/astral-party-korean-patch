from uuid import uuid4

import pytest

from astral_builder.database.repository import AssetLocationInput
from astral_builder.database.translations import TranslationWrite


def test_asset_location_validation_accepts_expected_origins_and_hash() -> None:
    location = AssetLocationInput(
        logical_name="lang.en",
        catalog_key="English",
        origin="remote",
        asset_type="TextAsset",
        asset_name="English",
        source_sha256="a" * 64,
        source_size=123,
    )
    location.validate()


def test_asset_location_validation_rejects_invalid_hash_or_origin() -> None:
    with pytest.raises(ValueError, match="origin"):
        AssetLocationInput(
            logical_name="x",
            catalog_key="x",
            origin="unknown",
            asset_type="TextAsset",
            asset_name="X",
        ).validate()
    with pytest.raises(ValueError, match="SHA-256"):
        AssetLocationInput(
            logical_name="x",
            catalog_key="x",
            origin="remote",
            asset_type="TextAsset",
            asset_name="X",
            source_sha256="BAD",
        ).validate()


def test_translation_write_validation() -> None:
    TranslationWrite(
        unit_id=uuid4(),
        locale="ko",
        text="번역",
        status="approved",
        source_fingerprint="b" * 64,
        actor="tester",
    ).validate()

    with pytest.raises(ValueError, match="status"):
        TranslationWrite(
            unit_id=uuid4(),
            locale="ko",
            text="번역",
            status="invalid",
            source_fingerprint="b" * 64,
        ).validate()
