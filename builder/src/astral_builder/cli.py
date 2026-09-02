from __future__ import annotations

import argparse
import json
import os
from uuid import UUID

import psycopg

from astral_builder import __version__
from astral_builder.automation.build import build_patch, write_build_github_output
from astral_builder.automation.check import check_revision, write_github_output
from astral_builder.automation.release import update_release_index
from astral_builder.automation.sync import (
    load_route_sync_config,
    persist_prepared_revision,
    prepare_revision,
    write_sync_github_output,
)
from astral_builder.automation.translation_status import (
    summarize_translation_snapshot,
    write_translation_status_github_output,
)
from astral_builder.automation.validate_build import validate_built_patch
from astral_builder.database.builds import set_build_status
from astral_builder.database.repository import load_translation_snapshot


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
    sync.add_argument("--github-output")

    build = subparsers.add_parser("build", help="Build and validate a complete patch manifest.")
    build.add_argument("--revision-id", required=True)
    build.add_argument("--route-config", required=True)
    build.add_argument("--work-dir", default=".work/build")
    build.add_argument("--output-dir", default="output/patch")
    build.add_argument("--asset-base-url", required=True)
    build.add_argument("--source-asset-base-url", required=True)
    build.add_argument("--patch-version", required=True)
    build.add_argument("--github-run-id")
    build.add_argument("--git-commit")
    build.add_argument("--legacy-data")
    build.add_argument("--github-output")

    validate_build = subparsers.add_parser(
        "validate-build", help="Validate a built patch in a fresh process."
    )
    validate_build.add_argument("--manifest", required=True)
    validate_build.add_argument("--assets-dir", required=True)
    validate_build.add_argument("--route-config", required=True)

    update_index = subparsers.add_parser(
        "update-index", help="Upsert a manifest into release index."
    )
    update_index.add_argument("--manifest", required=True)
    update_index.add_argument("--manifest-url", required=True)
    update_index.add_argument("--index", required=True)

    translation_status = subparsers.add_parser(
        "translation-status",
        help="Report canonical translation readiness without blocking safe source fallback.",
    )
    translation_status.add_argument("--revision-id", required=True)
    translation_status.add_argument("--github-output")
    translation_status.add_argument(
        "--strict",
        action="store_true",
        help="Fail when any current unit has no approved production translation.",
    )

    mark_released = subparsers.add_parser(
        "mark-released",
        help="Mark a database build as released.",
    )
    mark_released.add_argument("--build-id", required=True)
    return parser


def _database_url(*, direct: bool = False) -> str:
    if direct:
        value = os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL")
        if not value:
            raise SystemExit("DATABASE_URL_DIRECT or DATABASE_URL is required")
        return value
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
                "syncRequired": result.sync_required,
                "revisionId": result.revision_id,
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

    def progress(message: str) -> None:
        print(f"[sync:{config.route}] {message}", flush=True)

    progress("start")
    prepared = prepare_revision(
        config=config,
        game_version=args.game_version,
        work_dir=args.work_dir,
        progress=progress,
    )
    progress("persist prepared revision to database")
    with psycopg.connect(_database_url(direct=True)) as conn:
        result = persist_prepared_revision(conn, prepared, progress=progress)
    print(
        f"[sync:{config.route}] database sync complete: "
        f"+{result.source_added_count} ~{result.source_modified_count} "
        f"-{result.source_removed_count}",
        flush=True,
    )
    if args.github_output:
        write_sync_github_output(result, prepared, args.github_output)
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
                "sourceAddedCount": result.source_added_count,
                "sourceModifiedCount": result.source_modified_count,
                "sourceRemovedCount": result.source_removed_count,
                "assetLocationCount": result.asset_location_count,
                "downloadedBundleCount": result.downloaded_bundle_count,
                "emptyStrAssets": list(result.empty_str_assets),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _run_build(args: argparse.Namespace) -> int:
    with psycopg.connect(_database_url()) as conn:
        result = build_patch(
            conn,
            revision_id=UUID(args.revision_id),
            route_config=args.route_config,
            work_dir=args.work_dir,
            output_dir=args.output_dir,
            asset_base_url=args.asset_base_url,
            source_asset_base_url=args.source_asset_base_url,
            patch_version=args.patch_version,
            github_run_id=args.github_run_id,
            git_commit=args.git_commit,
            legacy_data_path=args.legacy_data,
        )
    if args.github_output:
        write_build_github_output(result, args.github_output)
    print(
        json.dumps(
            {
                "buildId": str(result.build_id),
                "manifest": str(result.manifest),
                "translationFingerprint": result.translation_fingerprint,
                "fileCount": len(result.files),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _run_validate_build(args: argparse.Namespace) -> int:
    result = validate_built_patch(
        args.manifest,
        args.assets_dir,
        route_config=args.route_config,
    )
    print(
        json.dumps(
            {
                "fileCount": result.file_count,
                "langKeys": result.lang_keys,
                "strAssets": result.str_assets,
                "strUnits": result.str_units,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _run_update_index(args: argparse.Namespace) -> int:
    index = update_release_index(
        manifest_path=args.manifest,
        manifest_url=args.manifest_url,
        index_path=args.index,
    )
    print(json.dumps({"releaseCount": len(index.releases), "index": args.index}, sort_keys=True))
    return 0


def _run_translation_status(args: argparse.Namespace) -> int:
    with psycopg.connect(_database_url()) as conn:
        snapshot = load_translation_snapshot(conn, UUID(args.revision_id))
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                FROM translation_changes tc
                JOIN translation_units tu ON tu.id = tc.unit_id
                WHERE tc.locale = 'ko'
                  AND tc.status = 'pending'
                  AND tu.current_source_version_id IS NOT NULL
                """
            )
            pending = int(cur.fetchone()[0])
    report = summarize_translation_snapshot(snapshot, pending=pending)
    if not report.releasable:
        raise SystemExit(f"Revision {args.revision_id} has no translation units")
    if args.github_output:
        write_translation_status_github_output(report, args.github_output)
    payload = {
        "total": report.total,
        "approved": report.approved,
        "untranslated": report.untranslated,
        "pending": report.pending,
        "incomplete": report.incomplete,
        "examples": list(report.examples),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if report.incomplete:
        print(
            "Translation warning: "
            f"{report.incomplete}/{report.total} units have no approved translation; "
            "they will keep the original game text."
        )
        for example in report.examples:
            print(f"  - {example}")
    if args.strict and report.incomplete:
        raise SystemExit(
            "Strict translation check failed: "
            f"{report.incomplete}/{report.total} units are incomplete"
        )
    return 0


def _run_mark_released(args: argparse.Namespace) -> int:
    with psycopg.connect(_database_url()) as conn:
        set_build_status(conn, UUID(args.build_id), "released")
    print(json.dumps({"buildId": args.build_id, "status": "released"}, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        return _run_check(args)
    if args.command == "sync":
        return _run_sync(args)
    if args.command == "build":
        return _run_build(args)
    if args.command == "validate-build":
        return _run_validate_build(args)
    if args.command == "update-index":
        return _run_update_index(args)
    if args.command == "translation-status":
        return _run_translation_status(args)
    if args.command == "mark-released":
        return _run_mark_released(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
