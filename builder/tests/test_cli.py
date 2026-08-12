from astral_builder import __version__
from astral_builder.cli import build_parser


def test_version_is_defined() -> None:
    assert __version__ == "0.1.0"


def test_parser_exposes_check_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["check", "--game-version", "3.2.0"])
    assert args.command == "check"
    assert args.game_version == "3.2.0"


def test_parser_exposes_sync_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "sync",
            "--game-version",
            "3.2.0",
            "--route-config",
            "routes/int_steam.yaml",
        ]
    )
    assert args.command == "sync"
    assert args.game_version == "3.2.0"


def test_parser_exposes_build_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "build",
            "--revision-id",
            "00000000-0000-0000-0000-000000000001",
            "--route-config",
            "routes/int_steam.yaml",
            "--asset-base-url",
            "https://example.test/release",
            "--patch-version",
            "v1",
            "--build-id",
            "build-1",
            "--legacy-data",
            "data.unity3d",
        ]
    )
    assert args.command == "build"
    assert args.channel == "preview"


def test_parser_exposes_validate_build_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "validate-build",
            "--manifest",
            "output/patch/manifest.json",
            "--assets-dir",
            "output/patch/assets",
            "--route-config",
            "routes/int_steam.yaml",
        ]
    )
    assert args.command == "validate-build"
