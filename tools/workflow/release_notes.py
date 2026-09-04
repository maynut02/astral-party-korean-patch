from __future__ import annotations

import argparse
from pathlib import Path

ROUTES = ("INT_STEAM", "CN_STEAM", "INT_ANDROID", "CN_ANDROID")


def _run_link(repository: str, run_id: str, run_number: str) -> str:
    return f"[GitHub Actions #{run_number}](https://github.com/{repository}/actions/runs/{run_id})"


def render_patch_notes(
    *,
    updated_routes: str,
    game_version: str,
    revisions: dict[str, str],
    repository: str,
    run_id: str,
    run_number: str,
) -> str:
    updated = [item.strip() for item in updated_routes.split(",") if item.strip()]
    unknown = sorted(set(updated) - set(ROUTES))
    if unknown:
        raise ValueError(f"unknown updated routes: {', '.join(unknown)}")
    missing = [route for route in ROUTES if not revisions.get(route, "").strip()]
    if missing:
        raise ValueError(f"missing route revisions: {', '.join(missing)}")

    lines = ["## 업데이트", ""]
    if updated:
        lines.extend(f"- `{route}`" for route in updated)
    else:
        lines.append("- 없음")

    lines.extend(
        [
            "",
            "## 플랫폼별 상태",
            "",
            "| 플랫폼 | 버전 | 리비전 |",
            "| --- | --- | --- |",
        ]
    )
    for route in ROUTES:
        lines.append(f"| `{route}` | `{game_version}` | `r{revisions[route]}` |")

    lines.extend(
        [
            "",
            "## 빌드",
            "",
            f"- {_run_link(repository, run_id, run_number)}",
            "",
        ]
    )
    return "\n".join(lines)


def render_original_backup_notes(
    *,
    version: str,
    revisions: dict[str, str],
    repository: str,
    run_id: str,
    run_number: str,
) -> str:
    missing = [route for route in ROUTES if not revisions.get(route, "").strip()]
    if missing:
        raise ValueError(f"missing route revisions: {', '.join(missing)}")
    lines = [
        "## 패치 복원용 원본",
        "",
        f"- 게임 버전: `{version}`",
        "- 한글패치를 제거할 때 복원하는 각 플랫폼의 변경 전 원본 파일입니다.",
        "",
        "## 플랫폼별 리비전",
        "",
        "| 플랫폼 | 리비전 |",
        "| --- | --- |",
    ]
    for route in ROUTES:
        lines.append(f"| `{route}` | `r{revisions[route]}` |")
    lines.extend(
        [
            "",
            "## 빌드",
            "",
            f"- {_run_link(repository, run_id, run_number)}",
            "",
        ]
    )
    return "\n".join(lines)


def _write(path: str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate concise GitHub release notes"
    )
    sub = parser.add_subparsers(dest="kind", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output", required=True)
    common.add_argument("--repository", required=True)
    common.add_argument("--run-id", required=True)
    common.add_argument("--run-number", required=True)

    patch = sub.add_parser("patch", parents=[common])
    patch.add_argument("--updated-routes", default="")
    patch.add_argument("--game-version", required=True)
    patch.add_argument("--int-steam-revision", required=True)
    patch.add_argument("--cn-steam-revision", required=True)
    patch.add_argument("--int-android-revision", required=True)
    patch.add_argument("--cn-android-revision", required=True)

    original_backup = sub.add_parser("original-backup", parents=[common])
    original_backup.add_argument("--version", required=True)
    original_backup.add_argument("--int-steam-revision", required=True)
    original_backup.add_argument("--cn-steam-revision", required=True)
    original_backup.add_argument("--int-android-revision", required=True)
    original_backup.add_argument("--cn-android-revision", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.kind == "patch":
        text = render_patch_notes(
            updated_routes=args.updated_routes,
            game_version=args.game_version,
            revisions={
                "INT_STEAM": args.int_steam_revision,
                "CN_STEAM": args.cn_steam_revision,
                "INT_ANDROID": args.int_android_revision,
                "CN_ANDROID": args.cn_android_revision,
            },
            repository=args.repository,
            run_id=args.run_id,
            run_number=args.run_number,
        )
    elif args.kind == "original-backup":
        text = render_original_backup_notes(
            version=args.version,
            revisions={
                "INT_STEAM": args.int_steam_revision,
                "CN_STEAM": args.cn_steam_revision,
                "INT_ANDROID": args.int_android_revision,
                "CN_ANDROID": args.cn_android_revision,
            },
            repository=args.repository,
            run_id=args.run_id,
            run_number=args.run_number,
        )
    else:
        raise ValueError(f"unsupported release note kind: {args.kind}")
    _write(args.output, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
