from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/workflow/notify_discord.py"
SPEC = importlib.util.spec_from_file_location("notify_discord", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _payload(updated_routes: str = "INT_STEAM,INT_ANDROID") -> dict[str, object]:
    return MODULE.build_payload(
        tag="v3.3.0_r001_p0",
        updated_routes=updated_routes,
        game_version="3.3.0",
        revisions={"INT_STEAM": "1", "CN_STEAM": "1", "INT_ANDROID": "2"},
        repository="owner/repo",
        run_id="123",
        timestamp="2026-09-03T00:00:00Z",
    )


def _fields(payload: dict[str, object]) -> dict[str, str]:
    embeds = payload["embeds"]
    assert isinstance(embeds, list)
    embed = embeds[0]
    assert isinstance(embed, dict)
    fields = embed["fields"]
    assert isinstance(fields, list)
    return {str(field["name"]): str(field["value"]) for field in fields}


def test_payload_contains_requested_release_information() -> None:
    payload = _payload()
    fields = _fields(payload)

    assert fields["버전"] == "`v3.3.0_r001_p0`"
    assert "Steam 글로벌" in fields["변경된 플랫폼"]
    assert "Android 일본" in fields["변경된 플랫폼"]
    assert "Steam 중국" not in fields["변경된 플랫폼"]
    assert "`v3.3.0 / r1`" in fields["현재 플랫폼별 버전"]
    assert "`v3.3.0 / r2`" in fields["현재 플랫폼별 버전"]
    assert "https://astral.maynutlab.com/patcher/INT_STEAM/install" in fields["패치 프로그램 / 앱"]
    assert "https://astral.maynutlab.com/patcher/CN_STEAM/install" in fields["패치 프로그램 / 앱"]
    assert "https://astral.maynutlab.com/android" in fields["패치 프로그램 / 앱"]
    assert "https://github.com/owner/repo/actions/runs/123" in fields["정보"]
    assert "https://github.com/owner/repo/releases/tag/v3.3.0_r001_p0" in fields["정보"]


def test_manual_rebuild_reports_no_platform_revision_change() -> None:
    fields = _fields(_payload(updated_routes=""))
    assert fields["변경된 플랫폼"] == "없음 (수동 재빌드)"


def test_payload_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="unknown updated routes"):
        _payload(updated_routes="UNKNOWN")
