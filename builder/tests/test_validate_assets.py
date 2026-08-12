import pytest

from astral_builder.formats.astral_str import StrDocument, StrEntry, encode_str
from astral_builder.formats.model import SourceStrings
from astral_builder.validate.assets import (
    ValidationError,
    validate_file,
    validate_lang_payload,
    validate_str_payload,
)


def test_validate_file_reports_sha_and_rejects_empty(tmp_path) -> None:
    path = tmp_path / "out.bin"
    path.write_bytes(b"abc")
    result = validate_file(path)
    assert "size=3" in result.details
    assert "sha256=" in result.details

    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    with pytest.raises(ValidationError, match="empty"):
        validate_file(empty)


def test_validate_lang_requires_same_key_order_and_set() -> None:
    original = '<resources><string name="A">1</string><string name="B">2</string></resources>'
    patched = '<resources><string name="A">x</string><string name="B">y</string></resources>'
    assert validate_lang_payload(original, patched).details == "keys=2"
    with pytest.raises(ValidationError, match="key"):
        validate_lang_payload(
            original,
            '<resources><string name="B">y</string><string name="A">x</string></resources>',
        )


def test_validate_str_preserves_id_sequence() -> None:
    original = encode_str(
        StrDocument(
            entries=(
                StrEntry(1, SourceStrings(en="one")),
                StrEntry(2, SourceStrings(en="two")),
            )
        )
    )
    patched = encode_str(
        StrDocument(
            entries=(
                StrEntry(1, SourceStrings(en="하나")),
                StrEntry(2, SourceStrings(en="둘")),
            )
        )
    )
    assert "entries=2" in validate_str_payload(original, patched).details
    changed = encode_str(StrDocument(entries=(StrEntry(2, SourceStrings(en="둘")),)))
    with pytest.raises(ValidationError, match="id"):
        validate_str_payload(original, changed)


def test_validate_empty_str() -> None:
    assert validate_str_payload(b"", b"").details == "entries=0"
