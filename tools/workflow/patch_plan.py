from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

ROUTES = ("INT_STEAM", "CN_STEAM", "INT_ANDROID", "CN_ANDROID")
SLUGS = {
    "INT_STEAM": "int_steam",
    "CN_STEAM": "cn_steam",
    "INT_ANDROID": "int_android",
    "CN_ANDROID": "cn_android",
}


@dataclass(frozen=True, slots=True)
class RouteCheck:
    route: str
    game_version: str
    revision: str
    revision_id: str
    changed: bool
    sync_required: bool


def _bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false: {value!r}")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key:
            raise ValueError(f"invalid output line in {path}: {raw!r}")
        values[key] = value
    return values


def load_check(path: Path) -> RouteCheck:
    values = read_env(path)
    route = values["route"].upper()
    if route not in ROUTES:
        raise ValueError(f"unsupported route in {path}: {route}")
    return RouteCheck(
        route=route,
        game_version=values["game_version"],
        revision=values["revision"],
        revision_id=values.get("revision_id", ""),
        changed=_bool(values["changed"], name="changed"),
        sync_required=_bool(values["sync_required"], name="sync_required"),
    )


def revision_key(value: str) -> tuple[tuple[int, int | str], ...]:
    parts = re.findall(r"\d+|\D+", value)
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold()) for part in parts
    )


def build_plan(checks: tuple[RouteCheck, ...]) -> dict[str, object]:
    by_route = {item.route: item for item in checks}
    if set(by_route) != set(ROUTES):
        missing = sorted(set(ROUTES) - set(by_route))
        extra = sorted(set(by_route) - set(ROUTES))
        raise ValueError(
            f"route checks are incomplete: missing={missing} extra={extra}"
        )

    versions = {item.game_version for item in checks}
    if len(versions) != 1:
        raise ValueError(f"route game versions differ: {sorted(versions)}")
    game_version = next(iter(versions))
    max_revision = max((item.revision for item in checks), key=revision_key)
    updated_routes = tuple(item.route for item in checks if item.changed)

    result: dict[str, object] = {
        # Every workflow invocation publishes a new immutable patch revision.
        # Automatic runs are already gated by the external watcher.
        "should_run": True,
        "game_version": game_version,
        "max_revision": max_revision,
        "updated_routes": updated_routes,
    }
    for route in ROUTES:
        item = by_route[route]
        slug = SLUGS[route]
        result[f"{slug}_revision"] = item.revision
        result[f"{slug}_revision_id"] = item.revision_id
        result[f"{slug}_changed"] = item.changed
        result[f"{slug}_sync_required"] = item.sync_required
    return result


def _write_output(path: Path, plan: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in plan.items():
            if key == "updated_routes":
                handle.write(
                    f"updated_routes_json={json.dumps(value, separators=(',', ':'))}\n"
                )
                handle.write(f"updated_routes_csv={','.join(value)}\n")
            elif isinstance(value, bool):
                handle.write(f"{key}={'true' if value else 'false'}\n")
            else:
                handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate per-route patch checks into one run plan."
    )
    parser.add_argument("--checks-dir", required=True, type=Path)
    parser.add_argument("--github-output", required=True, type=Path)
    args = parser.parse_args(argv)

    checks = tuple(
        load_check(args.checks_dir / f"{SLUGS[route]}.env") for route in ROUTES
    )
    plan = build_plan(checks)
    _write_output(args.github_output, plan)
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
