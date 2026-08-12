import pytest

from astral_builder.database.builds import BuildFileRecord


def test_build_file_record_validation() -> None:
    BuildFileRecord(
        target="addressables",
        relative_path="root/hash/__data",
        operation="replace",
        sha256="a" * 64,
        size=1,
    ).validate()


def test_build_file_record_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="traverse"):
        BuildFileRecord(
            target="game_data",
            relative_path="../data.unity3d",
            operation="replace",
            sha256="a" * 64,
            size=1,
        ).validate()
