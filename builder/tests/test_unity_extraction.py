from dataclasses import dataclass

import pytest

from astral_builder.extract.unity import (
    UnityExtractionError,
    extract_object_names,
    extract_text_assets,
)


@dataclass
class _Type:
    name: str


@dataclass
class _Asset:
    m_Name: str
    m_Script: bytes


class _Object:
    def __init__(self, type_name: str, asset: _Asset) -> None:
        self.type = _Type(type_name)
        self._asset = asset

    def read(self) -> _Asset:
        return self._asset


@dataclass
class _Environment:
    objects: list[_Object]


def _loader(_path: str) -> _Environment:
    return _Environment(
        objects=[
            _Object("TextAsset", _Asset("English", b"english")),
            _Object("TextAsset", _Asset("STRCard", b"str")),
            _Object("Texture2D", _Asset("ignored", b"texture")),
        ]
    )


def test_extracts_object_names_by_serialized_type() -> None:
    assert extract_object_names("fixture.bundle", type_name="Texture2D", loader=_loader) == (
        "ignored",
    )


def test_extracts_only_requested_text_assets() -> None:
    result = extract_text_assets("fixture.bundle", names=["English"], loader=_loader)
    assert result == {"English": b"english"}


def test_extracts_text_assets_by_prefix() -> None:
    result = extract_text_assets("fixture.bundle", name_prefix="STR", loader=_loader)
    assert result == {"STRCard": b"str"}


def test_missing_requested_asset_is_an_error() -> None:
    with pytest.raises(UnityExtractionError, match="not found"):
        extract_text_assets("fixture.bundle", names=["Missing"], loader=_loader)
