from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(name: str) -> str:
    return (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")


def test_patch_release_uses_generated_notes_file() -> None:
    text = _text("patch.yml")
    assert "tools/workflow/release_notes.py patch" in text
    assert "--int-steam-revision '${{ needs.state.outputs.int_steam_revision }}'" in text
    assert "--cn-steam-revision '${{ needs.state.outputs.cn_steam_revision }}'" in text
    assert "--int-android-revision '${{ needs.state.outputs.int_android_revision }}'" in text
    assert "--notes-file .work/patch-release-notes.md" in text


def test_autopatcher_release_uses_generated_notes_file() -> None:
    text = _text("autopatcher.yml")
    assert "tools/workflow/release_notes.py autopatcher" in text
    assert '--sha256 "$env:SHA256"' in text
    assert '--notes-file "$env:RUNNER_TEMP/autopatcher-release-notes.md"' in text


def test_android_release_uses_generated_notes_file() -> None:
    text = _text("int-android-apk.yml")
    assert "tools/workflow/release_notes.py android" in text
    assert "--game-version '${{ steps.apk.outputs.game_version }}'" in text
    assert "--notes-file .work/android-release-notes.md" in text
