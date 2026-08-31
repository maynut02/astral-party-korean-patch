from __future__ import annotations

import argparse
from pathlib import Path

ROUTES = ("INT_STEAM", "CN_STEAM", "INT_ANDROID")


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
            "## 현재 Route 상태",
            "",
            "| Route | 게임 버전 | Revision |",
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


def render_windows_patcher_notes(
    *,
    version: str,
    sha256: str,
    repository: str,
    run_id: str,
    run_number: str,
) -> str:
    return "\n".join(
        [
            "## WindowsPatcher",
            "",
            f"- 버전: `{version}`",
            "- 대상: Windows x64",
            "- `AstralWindowsPatcher.exe`를 내려받아 바로 실행하면 됩니다.",
            "- Steam 패치 설치/제거와 INT_APK 설치/업데이트를 지원합니다.",
            "",
            "## 확인",
            "",
            f"- SHA-256: `{sha256}`",
            "",
            "## 빌드",
            "",
            f"- {_run_link(repository, run_id, run_number)}",
            "",
        ]
    )


def render_android_patcher_notes(
    *,
    version: str,
    version_code: str,
    sha256: str,
    size: str,
    repository: str,
    run_id: str,
    run_number: str,
) -> str:
    return "\n".join(
        [
            "## AndroidPatcher",
            "",
            f"- 버전: `{version}` (`versionCode {version_code}`)",
            "- 대상: Android 11 이상",
            "- Shizuku를 사용해 최신 `INT_APK`를 다운로드하고 Google Play 설치 출처로 설치합니다.",
            "- Shizuku가 설치되어 있지 않으면 최신 안정 버전 다운로드를 안내합니다.",
            "- 새 AndroidPatcher 버전이 있으면 앱 안에서 업데이트를 감지합니다.",
            "",
            "## 설치 전 확인",
            "",
            "- Shizuku를 설치하고 무선 디버깅 또는 ADB로 Shizuku 서비스를 시작해야 합니다.",
            "- AndroidPatcher에 Shizuku 권한을 허용해야 게임 APK를 설치할 수 있습니다.",
            "",
            "## 파일 확인",
            "",
            f"- 파일: `AstralAndroidPatcher.apk`",
            f"- 크기: `{size}` bytes",
            f"- SHA-256: `{sha256}`",
            "",
            "## 빌드",
            "",
            f"- {_run_link(repository, run_id, run_number)}",
            "",
        ]
    )


def render_android_notes(
    *,
    game_version: str,
    sha256: str,
    repository: str,
    run_id: str,
    run_number: str,
) -> str:
    return "\n".join(
        [
            "## INT_APK",
            "",
            f"- 게임 버전: `{game_version}`",
            "- Android에서는 `AndroidPatcher`, Windows에서는 `WindowsPatcher`를 사용해 설치하세요.",
            "- 최초 리소스 다운로드나 인게임 업데이트 후 종료 안내가 표시되면 게임을 종료하고 다시 실행하세요.",
            "",
            "## 확인",
            "",
            f"- SHA-256: `{sha256}`",
            "",
            "## 빌드",
            "",
            f"- {_run_link(repository, run_id, run_number)}",
            "",
        ]
    )


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

    windows_patcher = sub.add_parser("windows-patcher", parents=[common])
    windows_patcher.add_argument("--version", required=True)
    windows_patcher.add_argument("--sha256", required=True)

    android_patcher = sub.add_parser("android-patcher", parents=[common])
    android_patcher.add_argument("--version", required=True)
    android_patcher.add_argument("--version-code", required=True)
    android_patcher.add_argument("--sha256", required=True)
    android_patcher.add_argument("--size", required=True)

    android = sub.add_parser("android", parents=[common])
    android.add_argument("--game-version", required=True)
    android.add_argument("--sha256", required=True)
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
            },
            repository=args.repository,
            run_id=args.run_id,
            run_number=args.run_number,
        )
    elif args.kind == "windows-patcher":
        text = render_windows_patcher_notes(
            version=args.version,
            sha256=args.sha256,
            repository=args.repository,
            run_id=args.run_id,
            run_number=args.run_number,
        )
    elif args.kind == "android-patcher":
        text = render_android_patcher_notes(
            version=args.version,
            version_code=args.version_code,
            sha256=args.sha256,
            size=args.size,
            repository=args.repository,
            run_id=args.run_id,
            run_number=args.run_number,
        )
    else:
        text = render_android_notes(
            game_version=args.game_version,
            sha256=args.sha256,
            repository=args.repository,
            run_id=args.run_id,
            run_number=args.run_number,
        )
    _write(args.output, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
