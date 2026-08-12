from __future__ import annotations

import argparse
import json
import os

import psycopg

from astral_builder import __version__
from astral_builder.automation.check import check_revision, write_github_output
from astral_builder.automation.sync import (
    load_route_sync_config,
    persist_prepared_revision,
    prepare_revision,
)


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

    sync = subparsers.add_parser("sync", help="Download, extract and persist one game revision.")
    sync.add_argument("--game-version", required=True)
    sync.add_argument("--route-config", required=True)
    sync.add_argument("--work-dir", default=".work/sync")
    return parser


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise SystemExit("DATABASE_URL is required")
    return value


def _run_check(args: argparse.Namespace) -> int:
    with psycopg.connect(_database_url()) as conn:
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


def _run_sync(args: argparse.Namespace) -> int:
    config = load_route_sync_config(args.route_config)
    prepared = prepare_revision(
        config=config,
        game_version=args.game_version,
        work_dir=args.work_dir,
    )
    with psycopg.connect(_database_url()) as conn:
        result = persist_prepared_revision(conn, prepared)
    print(
        json.dumps(
            {
                "revisionId": result.revision_id,
                "route": prepared.source.route,
                "gameVersion": prepared.source.version,
                "revision": prepared.source.revision,
                "catalogHash": prepared.catalog_hash,
                "idempotent": result.idempotent,
                "unitCount": result.unit_count,
                "assetLocationCount": result.asset_location_count,
                "downloadedBundleCount": result.downloaded_bundle_count,
                "emptyStrAssets": list(result.empty_str_assets),
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
    if args.command == "sync":
        return _run_sync(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
