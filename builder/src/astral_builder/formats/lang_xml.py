from __future__ import annotations

from collections.abc import Mapping
from xml.etree import ElementTree as ET

from astral_builder.formats.model import normalize_text


class LangXmlError(ValueError):
    """Raised when a language TextAsset is not valid Astral Party XML."""


def _decode_input(data: str | bytes) -> str:
    if isinstance(data, str):
        return data
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise LangXmlError("language XML is not valid UTF-8")


def decode_lang_xml(data: str | bytes) -> dict[str, str]:
    text = _decode_input(data)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise LangXmlError(f"invalid language XML: {exc}") from exc

    entries: dict[str, str] = {}
    for element in root.iter("string"):
        key = (element.attrib.get("name") or "").strip()
        if not key:
            raise LangXmlError("<string> element has no non-empty name attribute")
        if key in entries:
            raise LangXmlError(f"duplicate language key: {key}")
        entries[key] = normalize_text("".join(element.itertext()))
    if not entries:
        raise LangXmlError("language XML contains no <string> entries")
    return entries


def encode_lang_xml(
    entries: Mapping[str, str],
    *,
    root_tag: str = "resources",
    xml_declaration: bool = False,
) -> bytes:
    if not entries:
        raise LangXmlError("cannot encode an empty language mapping")
    root = ET.Element(root_tag)
    for key, value in entries.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            raise LangXmlError("language key cannot be empty")
        element = ET.SubElement(root, "string", {"name": normalized_key})
        element.text = normalize_text(str(value))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=xml_declaration)
