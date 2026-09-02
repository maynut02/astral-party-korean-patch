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


def _embed(payload: dict[str, object]) -> dict[str, object]:
    embeds = payload["embeds"]
    assert isinstance(embeds, list)
    embed = embeds[0]
    assert isinstance(embed, dict)
    return embed


def test_payload_uses_compact_description_style() -> None:
    payload = _payload()
    embed = _embed(payload)
    description = str(embed["description"])

    assert payload["content"] == (
        "@everyone\n새로운 한글패치 릴리즈가 등록되었습니다.\n`v3.3.0_r001_p0`"
    )
    assert payload["allowed_mentions"] == {"parse": ["everyone"]}
    assert description.startswith("# v3.3.0\\_r001\\_p0")
    assert "**변경된 플랫폼**" in description
    assert "✦ Steam 글로벌 버전" in description
    assert "✦ Android 일본 버전" in description
    changed_section = description.split("**변경된 플랫폼**", 1)[1].split(
        "**현재 플랫폼 버전**", 1
    )[0]
    assert "Steam 중국 버전" not in changed_section
    assert "✦ Steam 글로벌: `v3.3.0_r1`" in description
    assert "✦ Steam 중국: `v3.3.0_r1`" in description
    assert "✦ Android 일본: `v3.3.0_r2`" in description
    assert "https://astral.maynutlab.com/patcher/INT_STEAM/install" in description
    assert "https://astral.maynutlab.com/patcher/CN_STEAM/install" in description
    assert "https://astral.maynutlab.com/android" in description
    assert "https://astral.maynutlab.com/download" in description
    assert "https://github.com/owner/repo/actions/runs/123" in description
    assert "https://github.com/owner/repo/releases/tag/v3.3.0_r001_p0" in description
    assert embed["color"] == 14509728
    assert embed["timestamp"] == "2026-09-03T00:00:00Z"
    assert "fields" not in embed


def test_manual_rebuild_reports_no_platform_revision_change() -> None:
    description = str(_embed(_payload(updated_routes=""))["description"])
    assert "✦ 없음 (수동 재빌드)" in description


def test_payload_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="unknown updated routes"):
        _payload(updated_routes="UNKNOWN")
