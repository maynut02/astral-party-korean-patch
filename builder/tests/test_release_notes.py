from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/workflow/release_notes.py"
SPEC = importlib.util.spec_from_file_location("release_notes", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_patch_notes_put_updated_routes_before_platform_status() -> None:
    text = MODULE.render_patch_notes(
        updated_routes="INT_ANDROID,INT_STEAM",
        game_version="3.2.0",
        revisions={
            "INT_STEAM": "120",
            "CN_STEAM": "119",
            "INT_ANDROID": "121",
            "CN_ANDROID": "122",
        },
        repository="owner/repo",
        run_id="123",
        run_number="45",
    )
    assert text.index("## 업데이트") < text.index("## 플랫폼별 상태") < text.index("## 빌드")
    assert "| 플랫폼 | 버전 | 리비전 |" in text
    assert "- `INT_ANDROID`" in text
    assert "| `INT_STEAM` | `3.2.0` | `r120` |" in text
    assert "https://github.com/owner/repo/actions/runs/123" in text


def test_patch_notes_report_translation_change_without_updated_routes() -> None:
    text = MODULE.render_patch_notes(
        updated_routes="",
        game_version="3.2.0",
        revisions={
            "INT_STEAM": "120",
            "CN_STEAM": "119",
            "INT_ANDROID": "121",
            "CN_ANDROID": "122",
        },
        repository="owner/repo",
        run_id="123",
        run_number="45",
    )
    assert "- 번역 수정" in text


def test_patch_notes_reject_unknown_route() -> None:
    with pytest.raises(ValueError, match="unknown updated routes"):
        MODULE.render_patch_notes(
            updated_routes="UNKNOWN",
            game_version="3.2.0",
            revisions={"INT_STEAM": "1", "CN_STEAM": "1", "INT_ANDROID": "1", "CN_ANDROID": "1"},
            repository="owner/repo",
            run_id="1",
            run_number="1",
        )


def test_original_backup_notes_include_all_platform_revisions() -> None:
    text = MODULE.render_original_backup_notes(
        version="3.2.0",
        revisions={
            "INT_STEAM": "116",
            "CN_STEAM": "115",
            "INT_ANDROID": "116",
            "CN_ANDROID": "117",
        },
        repository="owner/repo",
        run_id="14",
        run_number="6",
    )
    assert "## 패치 복원용 원본" in text
    assert "- 게임 버전: `3.2.0`" in text
    assert "| `INT_STEAM` | `r116` |" in text
    assert "| `CN_STEAM` | `r115` |" in text
    assert "| `INT_ANDROID` | `r116` |" in text
    assert "| `CN_ANDROID` | `r117` |" in text
    assert "/actions/runs/14" in text
