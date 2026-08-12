from astral_builder import __version__
from astral_builder.cli import build_parser


def test_version_is_defined() -> None:
    assert __version__ == "0.1.0"


def test_parser_exposes_check_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["check"])
    assert args.command == "check"
