#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_ROOT = "https://api.github.com"


class GitHubApiError(RuntimeError):
    pass


def _request(
    *,
    token: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object] | list[object] | None]:
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astral-party-korean-distribution-publisher",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            return exc.code, None
        raise GitHubApiError(f"GitHub API {method} {path} failed: HTTP {exc.code}: {raw}") from exc
    except urllib.error.URLError as exc:
        raise GitHubApiError(f"GitHub API {method} {path} failed: {exc}") from exc


def _ensure_branch(*, token: str, repo: str, branch: str, base_sha: str) -> None:
    branch_ref = urllib.parse.quote(f"heads/{branch}", safe="/")
    status, _ = _request(
        token=token,
        method="GET",
        path=f"/repos/{repo}/git/ref/{branch_ref}",
    )
    if status != 404:
        return
    _request(
        token=token,
        method="POST",
        path=f"/repos/{repo}/git/refs",
        payload={"ref": f"refs/heads/{branch}", "sha": base_sha},
    )


def _current_file_sha(*, token: str, repo: str, branch: str, remote_path: str) -> str | None:
    quoted = urllib.parse.quote(remote_path.strip("/"), safe="/")
    query = urllib.parse.urlencode({"ref": branch})
    status, data = _request(
        token=token,
        method="GET",
        path=f"/repos/{repo}/contents/{quoted}?{query}",
    )
    if status == 404:
        return None
    if not isinstance(data, dict):
        raise GitHubApiError(f"unexpected GitHub Contents response for {remote_path}")
    sha = data.get("sha")
    if not isinstance(sha, str) or not sha:
        raise GitHubApiError(f"GitHub Contents response has no SHA for {remote_path}")
    return sha


def publish_file(
    *,
    token: str,
    repo: str,
    branch: str,
    remote_path: str,
    source: Path,
    message: str,
    base_sha: str,
) -> None:
    payload_bytes = source.read_bytes()
    encoded = base64.b64encode(payload_bytes).decode("ascii")
    _ensure_branch(token=token, repo=repo, branch=branch, base_sha=base_sha)
    quoted = urllib.parse.quote(remote_path.strip("/"), safe="/")

    for attempt in range(2):
        current_sha = _current_file_sha(
            token=token,
            repo=repo,
            branch=branch,
            remote_path=remote_path,
        )
        payload: dict[str, object] = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if current_sha:
            payload["sha"] = current_sha
        try:
            _request(
                token=token,
                method="PUT",
                path=f"/repos/{repo}/contents/{quoted}",
                payload=payload,
            )
            return
        except GitHubApiError:
            if attempt == 1:
                raise
    raise AssertionError("unreachable")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish one mutable distribution metadata file.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--path", required=True)
    parser.add_argument("--branch", default="distribution")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--base-sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--message", default="chore: update distribution metadata")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN or GH_TOKEN is required")
    if not args.repo:
        raise SystemExit("--repo or GITHUB_REPOSITORY is required")
    if not args.base_sha:
        raise SystemExit("--base-sha or GITHUB_SHA is required")
    if not args.source.is_file():
        raise SystemExit(f"source file not found: {args.source}")
    publish_file(
        token=token,
        repo=args.repo,
        branch=args.branch,
        remote_path=args.path,
        source=args.source,
        message=args.message,
        base_sha=args.base_sha,
    )
    print(f"published {args.source} -> {args.branch}:{args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
