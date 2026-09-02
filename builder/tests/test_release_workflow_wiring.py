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


def test_android_apk_release_uses_consistent_title_and_generated_notes() -> None:
    text = _text("android-game-original.yml")
    assert text.startswith("name: Android APK\n")
    assert "tools/workflow/release_notes.py android-apk" in text
    assert '--title "apk-INT_ANDROID v${VERSION_NAME}"' in text
    assert "Astral Party Android original" not in text
    assert "Astral Party Google Play 원본" not in text
