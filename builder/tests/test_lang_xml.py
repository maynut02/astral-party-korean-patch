import pytest

from astral_builder.formats.lang_xml import LangXmlError, decode_lang_xml, encode_lang_xml


def test_decode_language_xml_and_normalize_newlines() -> None:
    data = (
        '<resources><string name="A">hello\r\nworld</string>'
        '<string name="B"></string></resources>'
    )
    assert decode_lang_xml(data) == {"A": "hello\nworld", "B": ""}


def test_encode_language_xml_escapes_content_and_round_trips() -> None:
    encoded = encode_lang_xml({"A": 'a < b & "quoted"', "B": "line1\rline2"})
    assert decode_lang_xml(encoded) == {"A": 'a < b & "quoted"', "B": "line1\nline2"}


def test_decode_rejects_duplicate_language_key() -> None:
    with pytest.raises(LangXmlError, match="duplicate"):
        decode_lang_xml(
            '<resources><string name="A">1</string>'
            '<string name="A">2</string></resources>'
        )
