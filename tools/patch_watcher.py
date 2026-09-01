from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg


ROUTES = ("INT_STEAM", "CN_STEAM", "INT_ANDROID")
LOCK_ID = 1_387_426_501
USER_AGENT = "astral-party-patch-watcher/1.0"
DISPATCH_RETRY_AFTER = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class RemoteState:
    route: str
    game_version: str
    revision: str
    catalog_hash: str
    source_url: str

    @property
    def fingerprint(self) -> tuple[str, str, str]:
        return self.game_version, self.revision, self.catalog_hash


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    fingerprint: tuple[str, str, str]
    dispatched_at: datetime


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _fetch(url: str, *, timeout: float = 15.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"request failed: {url}: {exc}") from exc


def _host_for_route(route: str) -> str:
    if route.startswith("INT_"):
        return "selist.feimogames.com"
    if route.startswith("CN_"):
        return "se-web-cn.feimogames.com"
    raise RuntimeError(f"unsupported route: {route}")


def discover_remote_state(route: str, game_version: str) -> RemoteState:
    query = urllib.parse.urlencode({"route": route, "version": game_version})
    hotaddress_url = f"http://{_host_for_route(route)}:7878/api/hotaddressExtend/get?{query}"
    try:
        payload = json.loads(_fetch(hotaddress_url).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid hotaddress response for {route}") from exc

    source_url = payload.get("sourceUrl") if isinstance(payload, dict) else None
    if not isinstance(source_url, str) or not source_url.strip():
        raise RuntimeError(f"hotaddress response has no sourceUrl for {route}")
    source_url = source_url.rstrip("/")
    path_parts = [part for part in urllib.parse.urlparse(source_url).path.split("/") if part]
    if not path_parts:
        raise RuntimeError(f"sourceUrl has no revision for {route}: {source_url}")
    revision = path_parts[-1]

    hash_url = f"{source_url}/catalog_{game_version}.hash"
    try:
        catalog_hash = _fetch(hash_url).decode("ascii").strip().lower()
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"catalog hash is not ASCII for {route}") from exc
    if len(catalog_hash) != 32 or any(char not in "0123456789abcdef" for char in catalog_hash):
        raise RuntimeError(f"invalid catalog hash for {route}: {catalog_hash!r}")

    return RemoteState(route, game_version, revision, catalog_hash, source_url)


def _load_config(conn: psycopg.Connection) -> tuple[str, bool]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT game_version, enabled FROM patch_watch_config WHERE singleton = true"
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("patch_watch_config is missing")
    return str(row[0]), bool(row[1])


def _load_observed(conn: psycopg.Connection, route: str) -> tuple[str, str, str] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT game_version, revision, catalog_hash
            FROM patch_watch_routes
            WHERE route = %s
            """,
            (route,),
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0]), str(row[1]), str(row[2])


def _load_last_dispatched(conn: psycopg.Connection, route: str) -> DispatchRecord | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                last_dispatched_game_version,
                last_dispatched_revision,
                last_dispatched_catalog_hash,
                dispatched_at
            FROM patch_watch_routes
            WHERE route = %s
            """,
            (route,),
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return DispatchRecord(
        fingerprint=(str(row[0]), str(row[1]), str(row[2])),
        dispatched_at=row[3],
    )


def _load_latest_processed(
    conn: psycopg.Connection, route: str, game_version: str
) -> tuple[str, str, str] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT game_version, revision, catalog_build_hash
            FROM game_revisions
            WHERE route = %s
              AND game_version = %s
              AND processed_at IS NOT NULL
              AND catalog_build_hash IS NOT NULL
            ORDER BY detected_at DESC, processed_at DESC, id DESC
            LIMIT 1
            """,
            (route, game_version),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return str(row[0]), str(row[1]), str(row[2])


def _store_observed(conn: psycopg.Connection, state: RemoteState) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE patch_watch_routes
            SET game_version = %s,
                revision = %s,
                catalog_hash = %s,
                source_url = %s,
                observed_at = now()
            WHERE route = %s
            """,
            (
                state.game_version,
                state.revision,
                state.catalog_hash,
                state.source_url,
                state.route,
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"patch watch route is missing: {state.route}")


def _mark_dispatched(conn: psycopg.Connection, states: tuple[RemoteState, ...]) -> None:
    with conn.cursor() as cur:
        for state in states:
            cur.execute(
                """
                UPDATE patch_watch_routes
                SET last_dispatched_game_version = %s,
                    last_dispatched_revision = %s,
                    last_dispatched_catalog_hash = %s,
                    dispatched_at = now()
                WHERE route = %s
                """,
                (*state.fingerprint, state.route),
            )


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    return max(0, round((finished_at - started_at).total_seconds() * 1000))


def _create_run(conn: psycopg.Connection, started_at: datetime) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO patch_watch_runs (status, started_at)
            VALUES ('running', %s)
            RETURNING id
            """,
            (started_at,),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("failed to create patch watcher run")
    return int(row[0])


def _set_run_game_version(
    conn: psycopg.Connection, run_id: int, game_version: str
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE patch_watch_runs SET game_version = %s WHERE id = %s",
            (game_version, run_id),
        )


def _finish_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    status: str,
    started_at: datetime,
    workflow_dispatched: bool = False,
    error_message: str | None = None,
) -> None:
    finished_at = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE patch_watch_runs
            SET status = %s,
                workflow_dispatched = %s,
                finished_at = %s,
                duration_ms = %s,
                error_message = %s
            WHERE id = %s
            """,
            (
                status,
                workflow_dispatched,
                finished_at,
                _duration_ms(started_at, finished_at),
                error_message,
                run_id,
            ),
        )


def _start_route_run(
    conn: psycopg.Connection,
    run_id: int,
    route: str,
    game_version: str,
    started_at: datetime,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO patch_watch_run_routes (
                run_id,
                route,
                status,
                game_version,
                started_at
            )
            VALUES (%s, %s, 'checking', %s, %s)
            """,
            (run_id, route, game_version, started_at),
        )


def _finish_route_run(
    conn: psycopg.Connection,
    run_id: int,
    state: RemoteState | None,
    *,
    route: str,
    status: str,
    started_at: datetime,
    changed: bool,
    dispatch_due: bool,
    error_message: str | None = None,
) -> None:
    finished_at = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE patch_watch_run_routes
            SET status = %s,
                game_version = COALESCE(%s, game_version),
                revision = %s,
                catalog_hash = %s,
                source_url = %s,
                changed = %s,
                dispatch_due = %s,
                finished_at = %s,
                duration_ms = %s,
                error_message = %s
            WHERE run_id = %s AND route = %s
            """,
            (
                status,
                state.game_version if state is not None else None,
                state.revision if state is not None else None,
                state.catalog_hash if state is not None else None,
                state.source_url if state is not None else None,
                changed,
                dispatch_due,
                finished_at,
                _duration_ms(started_at, finished_at),
                error_message,
                run_id,
                route,
            ),
        )


def _needs_processing(
    state: RemoteState,
    *,
    processed: tuple[str, str, str] | None,
    previous_observed: tuple[str, str, str] | None,
    last_dispatched: DispatchRecord | None,
) -> bool:
    if processed is not None:
        return processed != state.fingerprint
    if last_dispatched is not None:
        return True
    if previous_observed is not None:
        return previous_observed != state.fingerprint
    return True


def _dispatch_due(
    state: RemoteState,
    *,
    processed: tuple[str, str, str] | None,
    previous_observed: tuple[str, str, str] | None,
    last_dispatched: DispatchRecord | None,
    now: datetime,
) -> bool:
    if not _needs_processing(
        state,
        processed=processed,
        previous_observed=previous_observed,
        last_dispatched=last_dispatched,
    ):
        return False
    if last_dispatched is None or last_dispatched.fingerprint != state.fingerprint:
        return True
    return now - last_dispatched.dispatched_at >= DISPATCH_RETRY_AFTER


def _route_status(
    state: RemoteState,
    *,
    processed: tuple[str, str, str] | None,
    previous_observed: tuple[str, str, str] | None,
    last_dispatched: DispatchRecord | None,
    now: datetime,
) -> tuple[bool, bool, str]:
    changed = _needs_processing(
        state,
        processed=processed,
        previous_observed=previous_observed,
        last_dispatched=last_dispatched,
    )
    if not changed:
        return False, False, "unchanged"
    dispatch_due = _dispatch_due(
        state,
        processed=processed,
        previous_observed=previous_observed,
        last_dispatched=last_dispatched,
        now=now,
    )
    if dispatch_due:
        return True, True, "change_detected"
    return True, False, "waiting_processing"


def _is_route_baseline(
    *,
    processed: tuple[str, str, str] | None,
    previous_observed: tuple[str, str, str] | None,
    last_dispatched: DispatchRecord | None,
) -> bool:
    return (
        processed is None
        and previous_observed is None
        and last_dispatched is None
    )


def _dispatch_patch(game_version: str) -> None:
    repository = os.environ.get("GITHUB_REPOSITORY", "maynut02/astral-party-korean-patch").strip()
    ref = os.environ.get("GITHUB_REF", "main").strip()
    token = _require_env("GITHUB_TOKEN")
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    url = f"{api_url}/repos/{repository}/actions/workflows/patch.yml/dispatches"
    body = json.dumps(
        {"ref": ref, "inputs": {"mode": "release", "game_version": game_version}}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15.0) as response:
            if response.status != 204:
                raise RuntimeError(f"unexpected GitHub dispatch status: {response.status}")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"GitHub workflow dispatch failed: {exc}") from exc


def run() -> int:
    import psycopg

    database_url = _require_env("DATABASE_URL")
    with psycopg.connect(database_url) as conn:
        run_started_at = datetime.now(timezone.utc)
        run_id: int | None = None
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,))
            locked = bool(cur.fetchone()[0])
        if not locked:
            run_id = _create_run(conn, run_started_at)
            _finish_run(
                conn,
                run_id,
                status="skipped_locked",
                started_at=run_started_at,
            )
            conn.commit()
            print("patch watcher is already running")
            return 0

        run_id = _create_run(conn, run_started_at)
        conn.commit()
        try:
            game_version, enabled = _load_config(conn)
            _set_run_game_version(conn, run_id, game_version)
            conn.commit()
            if not enabled:
                _finish_run(
                    conn,
                    run_id,
                    status="disabled",
                    started_at=run_started_at,
                )
                conn.commit()
                print("patch watcher is disabled")
                return 0

            previous_observed = {
                route: _load_observed(conn, route)
                for route in ROUTES
            }
            last_dispatched = {
                route: _load_last_dispatched(conn, route)
                for route in ROUTES
            }
            processed = {
                route: _load_latest_processed(conn, route, game_version)
                for route in ROUTES
            }
            baseline = all(previous_observed[route] is None for route in ROUTES) and all(
                processed[route] is None for route in ROUTES
            ) and all(last_dispatched[route] is None for route in ROUTES)

            states: list[RemoteState] = []
            changed: list[RemoteState] = []
            due: list[RemoteState] = []
            route_dispatch_due: dict[str, bool] = {}
            route_started_at: dict[str, datetime] = {}
            route_errors: dict[str, str] = {}
            now = datetime.now(timezone.utc)

            for route in ROUTES:
                started_at = datetime.now(timezone.utc)
                route_started_at[route] = started_at
                _start_route_run(conn, run_id, route, game_version, started_at)
                conn.commit()
                try:
                    state = discover_remote_state(route, game_version)
                    _store_observed(conn, state)
                    if _is_route_baseline(
                        processed=processed[route],
                        previous_observed=previous_observed[route],
                        last_dispatched=last_dispatched[route],
                    ):
                        route_changed = False
                        dispatch_due = False
                        route_status = "unchanged"
                    else:
                        route_changed, dispatch_due, route_status = _route_status(
                            state,
                            processed=processed[route],
                            previous_observed=previous_observed[route],
                            last_dispatched=last_dispatched[route],
                            now=now,
                        )
                    _finish_route_run(
                        conn,
                        run_id,
                        state,
                        route=route,
                        status=route_status,
                        started_at=started_at,
                        changed=route_changed,
                        dispatch_due=dispatch_due,
                    )
                    conn.commit()
                    states.append(state)
                    route_dispatch_due[route] = dispatch_due
                    if route_changed:
                        changed.append(state)
                    if dispatch_due:
                        due.append(state)
                except Exception as exc:
                    conn.rollback()
                    error_message = str(exc)
                    route_errors[route] = error_message
                    _finish_route_run(
                        conn,
                        run_id,
                        None,
                        route=route,
                        status="failed",
                        started_at=started_at,
                        changed=False,
                        dispatch_due=False,
                        error_message=error_message,
                    )
                    conn.commit()

            error_summary = "; ".join(
                f"{route}: {message}" for route, message in route_errors.items()
            ) or None

            if not states:
                _finish_run(
                    conn,
                    run_id,
                    status="failed",
                    started_at=run_started_at,
                    error_message=error_summary or "all route checks failed",
                )
                conn.commit()
                print("patch watcher failed: all route checks failed", file=sys.stderr)
                return 1

            if baseline:
                final_status = "partial_failure" if route_errors else "success"
                _finish_run(
                    conn,
                    run_id,
                    status=final_status,
                    started_at=run_started_at,
                    error_message=error_summary,
                )
                conn.commit()
                print(f"initialized patch watcher baseline for {game_version}")
                return 1 if route_errors else 0

            if not changed:
                final_status = "partial_failure" if route_errors else "success"
                _finish_run(
                    conn,
                    run_id,
                    status=final_status,
                    started_at=run_started_at,
                    error_message=error_summary,
                )
                conn.commit()
                print(f"no patch changes for {game_version}")
                return 1 if route_errors else 0

            changed_routes = ", ".join(state.route for state in changed)
            if not due:
                final_status = "partial_failure" if route_errors else "success"
                _finish_run(
                    conn,
                    run_id,
                    status=final_status,
                    started_at=run_started_at,
                    error_message=error_summary,
                )
                conn.commit()
                print(
                    f"patch change already dispatched for {game_version}: {changed_routes}; "
                    "waiting for processing"
                )
                return 1 if route_errors else 0

            print(f"patch change detected for {game_version}: {changed_routes}")
            try:
                _dispatch_patch(game_version)
            except Exception as exc:
                dispatch_error = str(exc)
                for state in due:
                    _finish_route_run(
                        conn,
                        run_id,
                        state,
                        route=state.route,
                        status="failed",
                        started_at=route_started_at[state.route],
                        changed=True,
                        dispatch_due=True,
                        error_message=dispatch_error,
                    )
                conn.commit()
                combined_error = "; ".join(
                    part
                    for part in (error_summary, f"workflow dispatch: {dispatch_error}")
                    if part
                )
                raise RuntimeError(combined_error) from exc

            _mark_dispatched(conn, tuple(states))
            for state in changed:
                _finish_route_run(
                    conn,
                    run_id,
                    state,
                    route=state.route,
                    status="dispatched",
                    started_at=route_started_at[state.route],
                    changed=True,
                    dispatch_due=route_dispatch_due[state.route],
                )
            final_status = "partial_failure" if route_errors else "success"
            _finish_run(
                conn,
                run_id,
                status=final_status,
                started_at=run_started_at,
                workflow_dispatched=True,
                error_message=error_summary,
            )
            conn.commit()
            print("Patch workflow dispatched")
            return 1 if route_errors else 0
        except Exception as exc:
            conn.rollback()
            try:
                _finish_run(
                    conn,
                    run_id,
                    status="failed",
                    started_at=run_started_at,
                    error_message=str(exc),
                )
                conn.commit()
            except Exception:
                conn.rollback()
            raise


def main() -> None:
    try:
        raise SystemExit(run())
    except Exception as exc:
        print(f"patch watcher failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
