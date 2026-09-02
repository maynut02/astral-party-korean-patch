from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from astral_builder.automation.reuse import resolve_released_revision, write_github_output


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.params = None

    def execute(self, _query, params):
        self.params = params

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.cursor_value = _Cursor(row)

    @contextmanager
    def cursor(self):
        yield self.cursor_value


def _manifest(path: Path, *, route: str = "INT_STEAM") -> Path:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "patch": {
                    "version": "v3.2.0_r120_p0",
                    "channel": "release",
                    "route": route,
                    "buildId": "build",
                    "translationFingerprint": "a" * 64,
                },
                "game": {
                    "version": "3.2.0",
                    "revision": "120",
                    "catalogHash": "b" * 32,
                },
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_reuse_release_resolves_exact_processed_revision(tmp_path: Path) -> None:
    revision_id = uuid4()
    conn = _Connection(
        (revision_id, "b" * 32, "https://cdn.example/120", "https://cdn.example/catalog", object())
    )
    state = resolve_released_revision(
        conn,
        manifest_path=_manifest(tmp_path / "manifest.json"),
        expected_route="INT_STEAM",
    )
    assert state.revision_id == str(revision_id)
    assert state.game_version == "3.2.0"
    assert state.revision == "120"
    assert conn.cursor_value.params == ("INT_STEAM", "3.2.0", "120")

    output = tmp_path / "state.env"
    write_github_output(state, output)
    text = output.read_text(encoding="utf-8")
    assert "changed=false" in text
    assert "sync_required=false" in text
    assert f"revision_id={revision_id}" in text


def test_reuse_release_rejects_route_mismatch(tmp_path: Path) -> None:
    conn = _Connection(None)
    with pytest.raises(ValueError, match="route mismatch"):
        resolve_released_revision(
            conn,
            manifest_path=_manifest(tmp_path / "manifest.json", route="CN_STEAM"),
            expected_route="INT_STEAM",
        )


def test_reuse_release_rejects_catalog_mismatch(tmp_path: Path) -> None:
    conn = _Connection(
        (uuid4(), "c" * 32, "https://cdn.example/120", "https://cdn.example/catalog", object())
    )
    with pytest.raises(RuntimeError, match="catalog hash differs"):
        resolve_released_revision(
            conn,
            manifest_path=_manifest(tmp_path / "manifest.json"),
            expected_route="INT_STEAM",
        )
