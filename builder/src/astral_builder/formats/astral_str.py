from __future__ import annotations

import struct
from dataclasses import dataclass

from astral_builder.formats.model import SourceStrings, normalize_text


class StrFormatError(ValueError):
    """Raised when an Astral Party STR TextAsset cannot be decoded safely."""


@dataclass(frozen=True, slots=True)
class StrEntry:
    id: int
    source: SourceStrings


@dataclass(frozen=True, slots=True)
class StrDocument:
    """Logical STR entries.

    Current game data stores every entry twice when ``paired`` is true: wrapper field 1 contains
    the direct entry and wrapper field 2 contains an id plus a nested mirror of that same entry.
    The codec collapses that representation to one logical StrEntry and reconstructs both copies.
    """

    entries: tuple[StrEntry, ...]
    paired: bool = True

    def by_id(self) -> dict[int, StrEntry]:
        return {entry.id: entry for entry in self.entries}


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if offset >= len(data):
            raise StrFormatError("unexpected end while reading varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
        shift += 7
    raise StrFormatError("varint exceeds 64-bit encoding length")


def _write_varint(value: int) -> bytes:
    if value < 0:
        raise StrFormatError("varint cannot encode a negative value")
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        output.append(byte | 0x80 if value else byte)
        if not value:
            return bytes(output)


def _read_length_delimited(data: bytes, offset: int, end: int) -> tuple[bytes, int]:
    length, offset = _read_varint(data, offset)
    payload_end = offset + length
    if payload_end > end:
        raise StrFormatError("length-delimited field exceeds message bounds")
    return data[offset:payload_end], payload_end


def _skip_field(data: bytes, offset: int, end: int, wire_type: int) -> int:
    if wire_type == 0:
        _, offset = _read_varint(data, offset)
        return offset
    if wire_type == 1:
        offset += 8
    elif wire_type == 2:
        _, offset = _read_length_delimited(data, offset, end)
        return offset
    elif wire_type == 5:
        offset += 4
    else:
        raise StrFormatError(f"unsupported protobuf wire type: {wire_type}")
    if offset > end:
        raise StrFormatError("protobuf field exceeds message bounds")
    return offset


def _decode_direct_entry(payload: bytes) -> StrEntry:
    offset = 0
    entry_id: int | None = None
    fields = {2: "", 3: "", 4: "", 5: ""}
    while offset < len(payload):
        tag, offset = _read_varint(payload, offset)
        field_number = tag >> 3
        wire_type = tag & 7
        if field_number == 1 and wire_type == 5:
            if offset + 4 > len(payload):
                raise StrFormatError("truncated STR id field")
            entry_id = struct.unpack_from("<I", payload, offset)[0]
            offset += 4
            continue
        if field_number in fields and wire_type == 2:
            raw, offset = _read_length_delimited(payload, offset, len(payload))
            try:
                fields[field_number] = normalize_text(raw.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise StrFormatError(f"STR field {field_number} is not UTF-8") from exc
            continue
        offset = _skip_field(payload, offset, len(payload), wire_type)

    if entry_id is None or entry_id == 0:
        raise StrFormatError("STR entry has no non-zero fixed32 id field")
    return StrEntry(
        id=entry_id,
        source=SourceStrings(
            cn_s=fields[2],
            en=fields[3],
            jp=fields[4],
            cn_t=fields[5],
        ),
    )


def _decode_mirror_entry(payload: bytes) -> StrEntry:
    offset = 0
    outer_id: int | None = None
    nested_payload: bytes | None = None
    while offset < len(payload):
        tag, offset = _read_varint(payload, offset)
        field_number = tag >> 3
        wire_type = tag & 7
        if field_number == 1 and wire_type == 5:
            if offset + 4 > len(payload):
                raise StrFormatError("truncated STR mirror id field")
            outer_id = struct.unpack_from("<I", payload, offset)[0]
            offset += 4
            continue
        if field_number == 2 and wire_type == 2:
            if nested_payload is not None:
                raise StrFormatError("STR mirror contains multiple nested entries")
            nested_payload, offset = _read_length_delimited(payload, offset, len(payload))
            continue
        offset = _skip_field(payload, offset, len(payload), wire_type)

    if outer_id is None or outer_id == 0 or nested_payload is None:
        raise StrFormatError("STR mirror is missing its id or nested entry")
    nested = _decode_direct_entry(nested_payload)
    if outer_id != nested.id:
        raise StrFormatError(f"STR mirror id mismatch: outer={outer_id}, nested={nested.id}")
    return nested


def decode_str(data: bytes) -> StrDocument:
    if not data:
        raise StrFormatError("STR payload is empty")

    offset = 0
    direct_entries: list[StrEntry] = []
    mirrors: dict[int, StrEntry] = {}
    seen_direct_ids: set[int] = set()
    saw_mirror = False

    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        field_number = tag >> 3
        wire_type = tag & 7
        if wire_type != 2 or field_number not in (1, 2):
            raise StrFormatError(
                f"unexpected STR wrapper field={field_number} wire_type={wire_type}"
            )
        payload, offset = _read_length_delimited(data, offset, len(data))

        if field_number == 1:
            entry = _decode_direct_entry(payload)
            if entry.id in seen_direct_ids:
                raise StrFormatError(f"duplicate direct STR id: {entry.id}")
            seen_direct_ids.add(entry.id)
            direct_entries.append(entry)
            continue

        saw_mirror = True
        mirror = _decode_mirror_entry(payload)
        if mirror.id in mirrors:
            raise StrFormatError(f"duplicate STR mirror id: {mirror.id}")
        mirrors[mirror.id] = mirror

    if not direct_entries:
        raise StrFormatError("STR payload contains no direct entries")

    if saw_mirror:
        if set(mirrors) != seen_direct_ids:
            missing = sorted(seen_direct_ids - set(mirrors))
            extra = sorted(set(mirrors) - seen_direct_ids)
            raise StrFormatError(
                f"STR direct/mirror id sets differ: missing={missing}, extra={extra}"
            )
        for direct in direct_entries:
            if mirrors[direct.id] != direct:
                raise StrFormatError(f"STR mirror content differs for id {direct.id}")

    return StrDocument(entries=tuple(direct_entries), paired=saw_mirror)


def _write_string_field(field_number: int, value: str) -> bytes:
    encoded = normalize_text(value).encode("utf-8")
    return _write_varint((field_number << 3) | 2) + _write_varint(len(encoded)) + encoded


def _encode_direct_entry(entry: StrEntry) -> bytes:
    source = entry.source.normalized()
    body = bytearray()
    body.extend(_write_varint((1 << 3) | 5))
    body.extend(struct.pack("<I", entry.id))
    body.extend(_write_string_field(2, source.cn_s))
    body.extend(_write_string_field(3, source.en))
    body.extend(_write_string_field(4, source.jp))
    body.extend(_write_string_field(5, source.cn_t))
    return bytes(body)


def _wrap(field_number: int, payload: bytes) -> bytes:
    return _write_varint((field_number << 3) | 2) + _write_varint(len(payload)) + payload


def encode_str(document: StrDocument) -> bytes:
    if not document.entries:
        raise StrFormatError("cannot encode an empty STR document")

    output = bytearray()
    seen_ids: set[int] = set()
    for entry in document.entries:
        if entry.id <= 0 or entry.id > 0xFFFFFFFF:
            raise StrFormatError(f"STR id is outside uint32 range: {entry.id}")
        if entry.id in seen_ids:
            raise StrFormatError(f"duplicate STR id: {entry.id}")
        seen_ids.add(entry.id)

        direct = _encode_direct_entry(entry)
        output.extend(_wrap(1, direct))
        if document.paired:
            mirror = bytearray()
            mirror.extend(_write_varint((1 << 3) | 5))
            mirror.extend(struct.pack("<I", entry.id))
            mirror.extend(_wrap(2, direct))
            output.extend(_wrap(2, bytes(mirror)))
    return bytes(output)
