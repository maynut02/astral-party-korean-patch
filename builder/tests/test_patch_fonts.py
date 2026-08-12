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
