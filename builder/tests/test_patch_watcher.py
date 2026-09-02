from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tools.patch_watcher import (
    DISPATCH_RETRY_AFTER,
    DispatchRecord,
    RemoteState,
    _dispatch_due,
    _is_route_baseline,
    _needs_processing,
    _parse_args,
    _route_status,
)


def test_interval_argument_enables_daemon_mode() -> None:
    args = _parse_args(["--interval-seconds", "300"])
    assert args.interval_seconds == 300


def test_interval_argument_must_be_positive() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--interval-seconds", "0"])


def _state(
    game_version: str = "3.2.0",
    revision: str = "116",
    catalog_hash: str = "a" * 32,
) -> RemoteState:
    return RemoteState(
        route="INT_STEAM",
        game_version=game_version,
        revision=revision,
        catalog_hash=catalog_hash,
        source_url=f"https://cdn.example/{revision}",
    )


def test_route_without_processed_or_observed_state_needs_processing() -> None:
    state = _state()
    assert _needs_processing(
        state,
        processed=None,
        previous_observed=None,
        last_dispatched=None,
    )


def test_database_game_version_change_triggers_dispatch() -> None:
    state = _state(game_version="3.3.0", revision="120", catalog_hash="b" * 32)
    previous = ("3.2.0", "116", "a" * 32)
    assert _needs_processing(
        state,
        processed=None,
        previous_observed=previous,
        last_dispatched=None,
    )


def test_processed_remote_fingerprint_does_not_dispatch() -> None:
    state = _state()
    assert not _needs_processing(
        state,
        processed=state.fingerprint,
        previous_observed=("3.1.0", "100", "b" * 32),
        last_dispatched=None,
    )


def test_recent_matching_dispatch_waits_for_patch_processing() -> None:
    state = _state()
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    record = DispatchRecord(
        fingerprint=state.fingerprint,
        dispatched_at=now - DISPATCH_RETRY_AFTER + timedelta(minutes=1),
    )
    assert not _dispatch_due(
        state,
        processed=None,
        previous_observed=state.fingerprint,
        last_dispatched=record,
        now=now,
    )


def test_stale_matching_dispatch_is_retried() -> None:
    state = _state()
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    record = DispatchRecord(
        fingerprint=state.fingerprint,
        dispatched_at=now - DISPATCH_RETRY_AFTER,
    )
    assert _dispatch_due(
        state,
        processed=None,
        previous_observed=state.fingerprint,
        last_dispatched=record,
        now=now,
    )


def test_route_status_is_unchanged_when_remote_state_is_processed() -> None:
    state = _state()
    changed, dispatch_due, status = _route_status(
        state,
        processed=state.fingerprint,
        previous_observed=None,
        last_dispatched=None,
        now=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    assert (changed, dispatch_due, status) == (False, False, "unchanged")


def test_route_status_waits_for_recent_matching_dispatch() -> None:
    state = _state()
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    record = DispatchRecord(
        fingerprint=state.fingerprint,
        dispatched_at=now - timedelta(minutes=1),
    )
    changed, dispatch_due, status = _route_status(
        state,
        processed=None,
        previous_observed=state.fingerprint,
        last_dispatched=record,
        now=now,
    )
    assert (changed, dispatch_due, status) == (
        True,
        False,
        "waiting_processing",
    )


def test_route_status_marks_new_remote_state_for_dispatch() -> None:
    state = _state(revision="117", catalog_hash="b" * 32)
    changed, dispatch_due, status = _route_status(
        state,
        processed=("3.2.0", "116", "a" * 32),
        previous_observed=("3.2.0", "116", "a" * 32),
        last_dispatched=None,
        now=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    assert (changed, dispatch_due, status) == (True, True, "change_detected")


def test_route_without_any_history_is_treated_as_baseline() -> None:
    assert _is_route_baseline(
        processed=None,
        previous_observed=None,
        last_dispatched=None,
    )


def test_route_with_existing_observation_is_not_baseline() -> None:
    assert not _is_route_baseline(
        processed=None,
        previous_observed=("3.2.0", "116", "a" * 32),
        last_dispatched=None,
    )
