from astral_builder.formats.model import SourceStrings, TranslationKind, TranslationUnit


def test_source_fingerprint_is_newline_normalized() -> None:
    a = SourceStrings(en="hello\r\nworld")
    b = SourceStrings(en="hello\nworld")
    assert a.fingerprint == b.fingerprint


def test_translation_unit_identity_is_stable() -> None:
    unit = TranslationUnit(
        kind=TranslationKind.STR,
        namespace="STRCard",
        key="1001",
        source=SourceStrings(en="Card"),
    )
    assert unit.identity == ("str", "STRCard", "1001")
