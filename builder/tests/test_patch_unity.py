from pathlib import Path
from types import SimpleNamespace

import pytest

from astral_builder.patch.unity import UnityPatchError, patch_text_assets


class FakeAsset:
    def __init__(self, name: str, script: str):
        self.m_Name = name
        self.m_Script = script
        self.saved = False

    def save(self) -> None:
        self.saved = True


class FakeObject:
    def __init__(self, asset: FakeAsset):
        self.asset = asset
        self.type = SimpleNamespace(name="TextAsset")

    def read(self) -> FakeAsset:
        return self.asset


class FakeFile:
    def __init__(self, data: bytes = b"bundle"):
        self.data = data

    def save(self) -> bytes:
        return self.data


class FakeEnvironment:
    def __init__(self, assets: list[FakeAsset]):
        self.objects = [FakeObject(asset) for asset in assets]
        self.file = FakeFile()


def test_patch_text_assets_replaces_and_saves(tmp_path: Path) -> None:
    asset = FakeAsset("English", "before")
    environments = [FakeEnvironment([asset]), FakeEnvironment([FakeAsset("English", "after")])]

    def loader(_: str):
        return environments.pop(0)

    output = tmp_path / "patched.bundle"
    patched = patch_text_assets(
        "input.bundle",
        output,
        {"English": b"after"},
        loader=loader,
    )
    assert patched == ("English",)
    assert asset.m_Script == "after"
    assert asset.saved
    assert output.read_bytes() == b"bundle"


def test_patch_text_assets_rejects_missing_target(tmp_path: Path) -> None:
    environment = FakeEnvironment([FakeAsset("Other", "value")])
    with pytest.raises(UnityPatchError, match="not found"):
        patch_text_assets(
            "input.bundle",
            tmp_path / "out.bundle",
            {"English": b"after"},
            loader=lambda _: environment,
        )


def test_binary_text_asset_round_trips_surrogate_bytes(tmp_path: Path) -> None:
    raw = b"\x0a\xc0\x00binary"
    script = raw.decode("utf-8", "surrogateescape")
    first = FakeEnvironment([FakeAsset("STRCard", "before")])
    second = FakeEnvironment([FakeAsset("STRCard", script)])
    environments = [first, second]

    output = tmp_path / "patched.bundle"
    patch_text_assets(
        "input.bundle",
        output,
        {"STRCard": raw},
        loader=lambda _: environments.pop(0),
    )
    assert first.objects[0].asset.m_Script.encode("utf-8", "surrogateescape") == raw
