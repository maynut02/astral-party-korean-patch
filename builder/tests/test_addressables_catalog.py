import base64
import json
import struct

from astral_builder.addressables import AddressablesCatalog


def _ascii(value: str) -> bytes:
    raw = value.encode("ascii")
    return b"\x00" + struct.pack("<i", len(raw)) + raw


def _catalog_json() -> str:
    keys = ["target.bundle", "English"]
    key_data = bytearray(struct.pack("<i", len(keys)))
    key_offsets = []
    for key in keys:
        key_offsets.append(len(key_data))
        key_data.extend(_ascii(key))

    # target.bundle -> entry 0, English -> entry 1
    bucket_data = bytearray(struct.pack("<i", 2))
    for key_offset, entry_index in zip(key_offsets, [0, 1], strict=True):
        bucket_data.extend(struct.pack("<iii", key_offset, 1, entry_index))

    entries = bytearray(struct.pack("<i", 2))
    # internal id, provider, dependency key, dependency hash, extra data, primary key, type
    entries.extend(struct.pack("<7i", 0, 0, -1, 0, -1, 0, 0))
    entries.extend(struct.pack("<7i", 1, 1, 0, 1234, -1, 1, 1))

    payload = {
        "m_LocatorId": "fixture",
        "m_BuildResultHash": "fixture-hash",
        "m_InternalIds": ["{Remote}/target.bundle", "english-internal-id"],
        "m_ProviderIds": ["AssetBundleProvider", "BundledAssetProvider"],
        "m_resourceTypes": [
            {
                "m_ClassName": (
                    "UnityEngine.ResourceManagement.ResourceProviders.IAssetBundleResource"
                )
            },
            {"m_ClassName": "UnityEngine.TextAsset"},
        ],
        "m_KeyDataString": base64.b64encode(key_data).decode(),
        "m_BucketDataString": base64.b64encode(bucket_data).decode(),
        "m_EntryDataString": base64.b64encode(entries).decode(),
    }
    return json.dumps(payload)


def test_locates_asset_and_bundle_dependency() -> None:
    catalog = AddressablesCatalog.from_json(_catalog_json())
    english = catalog.locate("English")
    assert len(english) == 1
    assert english[0].resource_type == "UnityEngine.TextAsset"
    assert english[0].dependency_key == "target.bundle"

    bundles = catalog.bundle_dependencies("English")
    assert len(bundles) == 1
    assert bundles[0].internal_id == "{Remote}/target.bundle"
    assert bundles[0].is_asset_bundle


def test_unknown_key_returns_empty_tuple() -> None:
    catalog = AddressablesCatalog.from_json(_catalog_json())
    assert catalog.locate("missing") == ()
    assert catalog.bundle_dependencies("missing") == ()
