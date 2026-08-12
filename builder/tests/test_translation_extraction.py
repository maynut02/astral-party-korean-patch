from astral_builder.extract.translations import extract_lang_units, extract_str_units
from astral_builder.formats.astral_str import StrDocument, StrEntry, encode_str
from astral_builder.formats.model import SourceStrings, TranslationKind


def _xml(values: dict[str, str]) -> str:
    body = "".join(f'<string name="{key}">{value}</string>' for key, value in values.items())
    return f"<resources>{body}</resources>"


def test_combines_four_language_assets_into_canonical_units() -> None:
    units = extract_lang_units(
        {
            "cn_s": _xml({"A": "甲", "B": "乙"}),
            "en": _xml({"A": "A", "B": "B"}),
            "jp": _xml({"A": "あ"}),
            "cn_t": _xml({"A": "甲繁"}),
        }
    )
    assert [unit.key for unit in units] == ["A", "B"]
    assert units[0].kind is TranslationKind.LANG
    assert units[0].source == SourceStrings(cn_s="甲", en="A", jp="あ", cn_t="甲繁")
    assert units[1].source.jp == ""


def test_extracts_str_assets_into_namespaced_units() -> None:
    payload = encode_str(
        StrDocument(entries=(StrEntry(1001, SourceStrings(en="Card", cn_s="卡")),))
    )
    units = extract_str_units({"Other": b"ignored", "STRCard": payload})
    assert len(units) == 1
    assert units[0].identity == ("str", "STRCard", "1001")
    assert units[0].source.en == "Card"


def test_empty_str_asset_is_zero_entries() -> None:
    units = extract_str_units({"STRDynamic": b""})
    assert units == ()
