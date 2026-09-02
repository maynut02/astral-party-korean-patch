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


def test_unified_original_release_uses_all_route_revisions() -> None:
    text = _text("patch.yml")
    assert "Publish or verify unified original release" in text
    assert "tools/workflow/release_notes.py original-backup" in text
    assert "--int-steam-revision '${{ needs.state.outputs.int_steam_revision }}'" in text
    assert "--cn-steam-revision '${{ needs.state.outputs.cn_steam_revision }}'" in text
    assert "--int-android-revision '${{ needs.state.outputs.int_android_revision }}'" in text
    assert "${{ needs.plan.outputs.original_tag }}" in text
