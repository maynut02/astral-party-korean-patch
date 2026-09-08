from astral_builder.patch.fonts import FontPatchResult


def test_font_patch_result_is_immutable_value_object(tmp_path) -> None:
    result = FontPatchResult(
        output_path=tmp_path / "font.bundle",
        target_name="Afacad-Regular",
        sha256="a" * 64,
        size=123,
    )
    assert result.target_name == "Afacad-Regular"
    assert result.size == 123


def test_legacy_font_patch_preserves_original_bundle_packer(tmp_path, monkeypatch) -> None:
    import astral_builder.patch.fonts as fonts

    class Asset:
        m_Name = "Afacad-Regular"
        m_FontData = b"old"

        def save(self) -> None:
            pass

    class Object:
        type = type("Type", (), {"name": "Font"})()

        def __init__(self, asset) -> None:
            self.asset = asset

        def read(self):
            return self.asset

    asset = Asset()
    environment = type("Environment", (), {"objects": [Object(asset)]})()
    captured: dict[str, str | None] = {}

    def save_environment(_environment, _output_path, *, packer=None):
        captured["packer"] = packer
        output = tmp_path / "data.unity3d"
        output.write_bytes(b"patched")
        return output

    monkeypatch.setattr(fonts, "_save_environment_atomic", save_environment)

    result = fonts.patch_legacy_font(
        tmp_path / "input.unity3d",
        tmp_path / "output.unity3d",
        font_name="Afacad-Regular",
        font_payload=b"new-font",
        loader=lambda _path: environment,
    )

    assert captured["packer"] == "original"
    assert asset.m_FontData == b"new-font"
    assert result.size == len(b"patched")
