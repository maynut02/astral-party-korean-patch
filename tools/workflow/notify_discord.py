from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime

ROUTE_LABELS = {
    "INT_STEAM": "Steam 글로벌",
    "CN_STEAM": "Steam 중국",
    "INT_ANDROID": "Android 일본",
}
ROUTES = tuple(ROUTE_LABELS)

WINDOWS_LINKS = {
    "INT_STEAM": "https://astral.maynutlab.com/patcher/INT_STEAM/install",
    "CN_STEAM": "https://astral.maynutlab.com/patcher/CN_STEAM/install",
}
ANDROID_APP_URL = "https://astral.maynutlab.com/android"


def _changed_platforms(updated_routes: str) -> str:
    routes = [item.strip() for item in updated_routes.split(",") if item.strip()]
    unknown = sorted(set(routes) - set(ROUTES))
    if unknown:
        raise ValueError(f"unknown updated routes: {', '.join(unknown)}")
    if not routes:
        return "없음 (수동 재빌드)"
    return "\n".join(f"- {ROUTE_LABELS[route]} (`{route}`)" for route in routes)


def _platform_versions(game_version: str, revisions: dict[str, str]) -> str:
    missing = [route for route in ROUTES if not revisions.get(route, "").strip()]
    if missing:
        raise ValueError(f"missing route revisions: {', '.join(missing)}")
    return "\n".join(
        f"- {ROUTE_LABELS[route]}: `v{game_version} / r{revisions[route]}`" for route in ROUTES
    )


def build_payload(
    *,
    tag: str,
    updated_routes: str,
    game_version: str,
    revisions: dict[str, str],
    repository: str,
    run_id: str,
    timestamp: str | None = None,
) -> dict[str, object]:
    if not all((tag.strip(), game_version.strip(), repository.strip(), run_id.strip())):
        raise ValueError("tag, game_version, repository and run_id are required")

    release_url = f"https://github.com/{repository}/releases/tag/{tag}"
    actions_url = f"https://github.com/{repository}/actions/runs/{run_id}"
    run_timestamp = timestamp or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    program_links = (
        f"[Steam 글로벌 실행]({WINDOWS_LINKS['INT_STEAM']}) | "
        f"[Steam 중국 실행]({WINDOWS_LINKS['CN_STEAM']})\n"
        f"[Android 패치 앱]({ANDROID_APP_URL})"
    )

    return {
        "content": "새로운 한글패치 릴리즈가 등록되었습니다.",
        "embeds": [
            {
                "title": tag,
                "url": release_url,
                "fields": [
                    {"name": "버전", "value": f"`{tag}`", "inline": False},
                    {
                        "name": "변경된 플랫폼",
                        "value": _changed_platforms(updated_routes),
                        "inline": False,
                    },
                    {
                        "name": "현재 플랫폼별 버전",
                        "value": _platform_versions(game_version, revisions),
                        "inline": False,
                    },
                    {
                        "name": "패치 프로그램 / 앱",
                        "value": program_links,
                        "inline": False,
                    },
                    {
                        "name": "정보",
                        "value": f"[GitHub Actions]({actions_url}) | [Release]({release_url})",
                        "inline": False,
                    },
                ],
                "timestamp": run_timestamp,
            }
        ],
    }


def send_webhook(webhook: str, payload: dict[str, object]) -> None:
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "astral-party-korean-patch/discord-notifier",
        },
    )
    with urllib.request.urlopen(request, timeout=15.0) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Discord webhook returned HTTP {response.status}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send a Discord notification for a patch release")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--updated-routes", default="")
    parser.add_argument("--game-version", required=True)
    parser.add_argument("--int-steam-revision", required=True)
    parser.add_argument("--cn-steam-revision", required=True)
    parser.add_argument("--int-android-revision", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    webhook = os.environ.get("DISCORD_WEBHOOK", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK이 설정되지 않아 Discord 알림을 건너뜁니다.")
        return 0

    payload = build_payload(
        tag=args.tag,
        updated_routes=args.updated_routes,
        game_version=args.game_version,
        revisions={
            "INT_STEAM": args.int_steam_revision,
            "CN_STEAM": args.cn_steam_revision,
            "INT_ANDROID": args.int_android_revision,
        },
        repository=args.repository,
        run_id=args.run_id,
    )
    try:
        send_webhook(webhook, payload)
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        print(f"::warning::Discord 알림 전송에 실패했습니다: {exc}")
        return 0

    print("Discord 릴리즈 알림을 전송했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
