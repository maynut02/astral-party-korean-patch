from __future__ import annotations

import argparse
import json
import os

import psycopg

from astral_builder import __version__
from astral_builder.automation.check import check_revision, write_github_output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astral-builder",
        description="Build automation for the Astral Party Korean patch.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    check = subparsers.add_parser("check", help="Check whether a new game revision exists.")
    check.add_argument("--route", default="INT_STEAM")
    check.add_argument("--game-version", required=True)
    check.add_argument("--github-output")
    return parser


def _run_check(args: argparse.Namespace) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    with psycopg.connect(database_url) as conn:
        result = check_revision(
            conn,
            route=args.route,
            game_version=args.game_version,
        )
    if args.github_output:
        write_github_output(result, args.github_output)
    print(
        json.dumps(
            {
                "changed": result.changed,
                "route": result.source.route,
                "gameVersion": result.source.version,
                "revision": result.source.revision,
                "catalogHash": result.catalog_hash,
                "sourceUrl": result.source.source_url,
                "catalogUrl": result.source.catalog_url,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        return _run_check(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
