from astral_builder.database.snapshot import SnapshotUnit, make_snapshot
from astral_builder.formats.astral_str import StrDocument, StrEntry, decode_str, encode_str
from astral_builder.formats.model import SourceStrings
from astral_builder.patch.translations import patch_lang_payload, patch_str_payload


def _snapshot_unit(*, kind: str, namespace: str, key: str, translation: str) -> SnapshotUnit:
    return SnapshotUnit(
        kind=kind,
        namespace=namespace,
        key=key,
        source=SourceStrings(en="source"),
        source_version_id=f"source-{kind}-{namespace}-{key}",
        translation=translation,
    )


def _snapshot(*units: SnapshotUnit):
    return make_snapshot(
        revision_id="rev-id",
        route="INT_STEAM",
        game_version="3.2.0",
        revision="1042",
        locale="ko",
        units=units,
    )


def test_lang_uses_production_translation_and_falls_back_to_source() -> None:
    snapshot = _snapshot(
        _snapshot_unit(kind="lang", namespace="lang", key="A", translation="승인 번역"),
        _snapshot_unit(kind="lang", namespace="lang", key="B", translation=""),
    )
    payload, stats = patch_lang_payload(
        '<resources><string name="A">A</string><string name="B">B</string></resources>',
        snapshot,
    )
    text = payload.decode()
    assert "승인 번역" in text
    assert ">B<" in text
    assert stats.translated_units == 1



def test_str_patch_replaces_only_configured_language_field() -> None:
    source = encode_str(
        StrDocument(
            entries=(StrEntry(1001, SourceStrings(cn_s="중", en="English", jp="日", cn_t="繁")),)
        )
    )
    snapshot = _snapshot(
        _snapshot_unit(kind="str", namespace="STRCard", key="1001", translation="한국어")
    )
    payload, stats = patch_str_payload(source, snapshot, namespace="STRCard", target_field="en")
    entry = decode_str(payload).entries[0]
    assert entry.source.en == "한국어"
    assert entry.source.cn_s == "중"
    assert entry.source.jp == "日"
    assert stats.translated_units == 1


def test_empty_str_payload_stays_empty() -> None:
    payload, stats = patch_str_payload(b"", _snapshot(), namespace="STRDynamic", target_field="en")
    assert payload == b""
    assert stats.total_units == 0


def test_str_patch_preserves_grouped_mirror_layout() -> None:
    source = encode_str(
        StrDocument(
            entries=(
                StrEntry(0, SourceStrings(en="Default")),
                StrEntry(1001, SourceStrings(en="Server Shut Down")),
            ),
            mirrors_grouped=True,
        )
    )
    payload, _stats = patch_str_payload(
        source, _snapshot(), namespace="STRServer", target_field="en"
    )
    assert decode_str(payload).mirrors_grouped is True
    assert payload == source
