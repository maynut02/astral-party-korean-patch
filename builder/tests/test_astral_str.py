import pytest

from astral_builder.formats.astral_str import (
    StrDocument,
    StrEntry,
    StrFormatError,
    decode_str,
    encode_str,
)
from astral_builder.formats.model import SourceStrings


def _document(*, paired: bool = True) -> StrDocument:
    return StrDocument(
        entries=(
            StrEntry(1001, SourceStrings(cn_s="简体", en="Card", jp="カード", cn_t="繁體")),
            StrEntry(1002, SourceStrings(cn_s="第二\r\n行", en="Second", jp="二", cn_t="第二")),
        ),
        paired=paired,
    )


def _normalized_document(*, paired: bool = True) -> StrDocument:
    return StrDocument(
        entries=(
            _document().entries[0],
            StrEntry(1002, SourceStrings(cn_s="第二\n行", en="Second", jp="二", cn_t="第二")),
        ),
        paired=paired,
    )


@pytest.mark.parametrize("paired", [False, True])
def test_str_round_trip(paired: bool) -> None:
    encoded = encode_str(_document(paired=paired))
    decoded = decode_str(encoded)
    assert decoded == _normalized_document(paired=paired)
    assert encode_str(decoded) == encoded


def test_str_rejects_duplicate_ids() -> None:
    document = StrDocument(entries=(_document().entries[0], _document().entries[0]))
    with pytest.raises(StrFormatError, match="duplicate"):
        encode_str(document)


def test_str_rejects_truncated_payload() -> None:
    encoded = encode_str(_document())
    with pytest.raises(StrFormatError):
        decode_str(encoded[:-1])


def test_str_rejects_changed_mirror_content() -> None:
    encoded = bytearray(encode_str(StrDocument(entries=(_document().entries[0],))))
    encoded[-1] ^= 1
    with pytest.raises(StrFormatError):
        decode_str(bytes(encoded))
