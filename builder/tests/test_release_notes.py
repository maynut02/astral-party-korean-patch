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
        revisions={"INT_STEAM": "120", "CN_STEAM": "119", "INT_ANDROID": "121"},
        repository="owner/repo",
        run_id="123",
        run_number="45",
    )
    assert text.index("## 업데이트") < text.index("## 플랫폼별 상태") < text.index("## 빌드")
    assert "| 플랫폼 | 버전 | 리비전 |" in text
    assert "- `INT_ANDROID`" in text
    assert "| `INT_STEAM` | `3.2.0` | `r120` |" in text
    assert "https://github.com/owner/repo/actions/runs/123" in text


def test_patch_notes_reject_unknown_route() -> None:
    with pytest.raises(ValueError, match="unknown updated routes"):
        MODULE.render_patch_notes(
            updated_routes="UNKNOWN",
            game_version="3.2.0",
            revisions={"INT_STEAM": "1", "CN_STEAM": "1", "INT_ANDROID": "1"},
            repository="owner/repo",
            run_id="1",
            run_number="1",
        )


def test_windows_patcher_notes_are_concise_and_include_hash_and_run() -> None:
    text = MODULE.render_windows_patcher_notes(
        version="0.8.7",
        sha256="a" * 64,
        repository="owner/repo",
        run_id="10",
        run_number="2",
    )
    assert "Windows x64" in text
    assert "AstralWindowsPatcher.exe" in text
    assert "a" * 64 in text
    assert "/actions/runs/10" in text


def test_android_patcher_notes_include_requirements_and_artifact_details() -> None:
    text = MODULE.render_android_patcher_notes(
        version="0.1.0",
        version_code="1000",
        sha256="c" * 64,
        size="123456",
        repository="owner/repo",
        run_id="12",
        run_number="4",
    )
    assert "Android 11" in text
    assert "Shizuku" in text
    assert "원본 게임" in text
    assert "Addressables" in text
    assert "AstralAndroidPatcher.apk" in text
    assert "123456" in text
    assert "c" * 64 in text
    assert "/actions/runs/12" in text


def test_android_apk_notes_use_original_title_and_artifact_details() -> None:
    text = MODULE.render_android_apk_notes(
        version="3.2.0",
        version_code="555",
        file_count="2",
        device_profile="px_9a",
        certificate_sha256="d" * 64,
        repository="owner/repo",
        run_id="13",
        run_number="5",
    )
    assert text.startswith("## Astral Party APK 원본\n")
    assert "Google Play 원본" not in text
    assert "병합, 압축 해제" not in text
    assert "`3.2.0` (`versionCode 555`)" in text
    assert "`2개`" in text
    assert "d" * 64 in text
    assert "/actions/runs/13" in text


def test_original_backup_notes_include_platform_version_and_revision() -> None:
    text = MODULE.render_original_backup_notes(
        platform="INT_ANDROID",
        version="3.2.0",
        revision="r116",
        repository="owner/repo",
        run_id="14",
        run_number="6",
    )
    assert "## 패치 복원용 원본" in text
    assert "- 플랫폼: `INT_ANDROID`" in text
    assert "- 버전: `3.2.0`" in text
    assert "- 리비전: `r116`" in text
    assert "/actions/runs/14" in text
