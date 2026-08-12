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
