from uuid import uuid4

import pytest

from astral_builder.database.repository import AssetLocationInput
from astral_builder.database.translations import TranslationChangeGroupWrite, TranslationProposal


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
        AssetLocationInput("x", "x", "unknown", "TextAsset", "X").validate()
    with pytest.raises(ValueError, match="SHA-256"):
        AssetLocationInput(
            "x", "x", "remote", "TextAsset", "X", source_sha256="BAD"
        ).validate()


def test_translation_change_inputs_require_meaningful_text() -> None:
    TranslationChangeGroupWrite(title="카드 번역 수정", actor="tester").validate()
    TranslationProposal(
        group_id=uuid4(),
        unit_id=uuid4(),
        locale="ko",
        source_version_id=uuid4(),
        text="번역",
        actor="tester",
    ).validate()

    with pytest.raises(ValueError, match="title"):
        TranslationChangeGroupWrite(title="   ").validate()
    with pytest.raises(ValueError, match="text"):
        TranslationProposal(uuid4(), uuid4(), "ko", uuid4(), "   ").validate()
