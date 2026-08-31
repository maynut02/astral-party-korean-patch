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


def test_windows_patcher_release_uses_generated_notes_file() -> None:
    text = _text("windows-patcher.yml")
    assert "tools/workflow/release_notes.py windows-patcher" in text
    assert '--sha256 "$env:SHA256"' in text
    assert '--notes-file "$env:RUNNER_TEMP/windows-patcher-release-notes.md"' in text
    assert "contents/patcher-index.json?ref=distribution" in text
    assert "windows-patcher-v" in text
    assert 'WindowsPatcher v$env:VERSION' in text
    assert "AstralWindowsPatcher.exe" in text


def test_android_patcher_release_uses_generated_notes_file() -> None:
    text = _text("android-patcher.yml")
    assert "tools/workflow/release_notes.py android-patcher" in text
    assert '--version-code "$VERSION_CODE"' in text
    assert '--notes-file "$RUNNER_TEMP/android-patcher-release-notes.md"' in text
    assert "contents/mobile-patcher-index.json?ref=distribution" in text
    assert "android-patcher-v" in text
    assert 'AndroidPatcher v${VERSION}' in text
    assert "AstralAndroidPatcher.apk" in text


def test_android_release_uses_generated_notes_file() -> None:
    text = _text("int-apk.yml")
    assert "tools/workflow/release_notes.py android" in text
    assert "--game-version '${{ steps.apk.outputs.game_version }}'" in text
    assert "--notes-file .work/android-release-notes.md" in text
    assert "int-apk-v" in text
    assert 'INT_APK v$GAME_VERSION' in text
    assert "AstralPartyKorean.apk" in text
