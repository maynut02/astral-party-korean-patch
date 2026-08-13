from astral_builder.database.legacy_import import (
    legacy_lang_translation,
    legacy_str_translation,
)
from astral_builder.formats.model import SourceStrings


def test_legacy_lang_translation_maps_identity_text_and_source() -> None:
    item = legacy_lang_translation(
        ("HELLO", "简", "Hello", "こんにちは", "繁", "  안녕\r\n하세요  ")
    )
    assert item is not None
    assert item.identity == ("lang", "lang", "HELLO")
    assert item.text == "안녕\n하세요"
    assert item.source_fingerprint == SourceStrings(
        cn_s="简", en="Hello", jp="こんにちは", cn_t="繁"
    ).fingerprint


def test_legacy_str_translation_maps_category_and_numeric_key() -> None:
    item = legacy_str_translation(("STRCard", 421, "简", "Card", "カード", "繁", "카드"))
    assert item is not None
    assert item.identity == ("str", "STRCard", "421")
    assert item.text == "카드"


def test_legacy_translation_skips_empty_identity_or_translation() -> None:
    assert legacy_lang_translation(("", "", "", "", "", "번역")) is None
    assert legacy_lang_translation(("KEY", "", "", "", "", "   ")) is None
    assert legacy_str_translation(("", 1, "", "", "", "", "번역")) is None
    assert legacy_str_translation(("STRCard", 1, "", "", "", "", "")) is None


def test_legacy_source_change_can_be_detected_against_current_source() -> None:
    item = legacy_str_translation(("STRCard", 7, "旧", "Old", "旧", "舊", "번역"))
    assert item is not None
    current = SourceStrings(cn_s="新", en="New", jp="新", cn_t="新").fingerprint
    assert item.source_fingerprint != current
