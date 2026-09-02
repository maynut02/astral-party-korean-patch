from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/patch.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_patch_workflow_is_split_into_parallel_roles() -> None:
    jobs = _workflow()["jobs"]
    assert set(jobs) == {
        "check",
        "plan",
        "sync",
        "state",
        "steam-inputs",
        "build-steam",
        "build-android",
        "publish",
        "index",
        "finalize",
    }
    check_routes = {item["route"] for item in jobs["check"]["strategy"]["matrix"]["include"]}
    sync_routes = {item["route"] for item in jobs["sync"]["strategy"]["matrix"]["include"]}
    assert check_routes == {"INT_STEAM", "CN_STEAM", "INT_ANDROID"}
    assert sync_routes == check_routes
    assert jobs["build-steam"]["strategy"]["matrix"]["include"][0]["route"] == "INT_STEAM"
    assert jobs["build-steam"]["strategy"]["matrix"]["include"][1]["route"] == "CN_STEAM"
    assert set(jobs["publish"]["needs"]) == {"plan", "state", "build-steam", "build-android"}


def test_patch_workflow_uses_only_immutable_release_tags() -> None:
    workflow = _workflow()
    triggers = workflow[True] if True in workflow else workflow["on"]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert "mode" not in inputs

    jobs = workflow["jobs"]
    plan_script = next(
        step["run"]
        for step in jobs["plan"]["steps"]
        if step.get("name") == "Define immutable release identity"
    )
    publish_script = next(
        step["run"]
        for step in jobs["publish"]["steps"]
        if step.get("name") == "Publish release"
    )
    assert "tools/workflow/release_tag.py" in plan_script
    assert "patch-pre" not in plan_script
    assert "--prerelease" not in publish_script
    assert "--channel" not in WORKFLOW.read_text(encoding="utf-8")
    assert "immutable release tag already exists" in publish_script


def test_patch_workflow_builds_against_immutable_original_releases() -> None:
    jobs = _workflow()["jobs"]
    steam_build = next(
        step["run"]
        for step in jobs["build-steam"]["steps"]
        if step.get("name") == "Build and validate"
    )
    android_build = next(
        step["run"]
        for step in jobs["build-android"]["steps"]
        if step.get("name") == "Build and validate"
    )
    original_publish = next(
        step["run"]
        for step in jobs["publish"]["steps"]
        if step.get("name") == "Publish immutable original releases"
    )

    assert "--source-asset-base-url" in steam_build
    assert "--source-asset-base-url" in android_build
    assert 'source_tag="original-${{ matrix.route }}-' in steam_build
    assert 'source_tag="original-INT_ANDROID-' in android_build
    assert (
        'original_tag="original-${route}-${game_version}_r${revision_number}"'
        in original_publish
    )
    assert 'if gh release view "$original_tag"' in original_publish
    assert "reusing immutable original release" in original_publish
    assert 'gh release create "$original_tag"' in original_publish
    assert (
        'original_title="original-${route} v${game_version}_r${revision_number}"'
        in original_publish
    )
    assert "tools/workflow/release_notes.py original-backup" in original_publish
    assert '--notes-file "$notes_file"' in original_publish
    assert "--clobber" not in original_publish
    assert "immutable original release is incomplete" in original_publish


def test_patch_workflow_has_no_github_cron_and_accepts_watcher_game_version() -> None:
    workflow = _workflow()
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert "schedule" not in triggers
    assert "workflow_dispatch" in triggers
    assert "game_version" in triggers["workflow_dispatch"]["inputs"]
    assert "inputs.game_version" in workflow["env"]["GAME_VERSION"]
    jobs = workflow["jobs"]
    aggregate_script = next(
        step["run"]
        for step in jobs["plan"]["steps"]
        if step.get("name") == "Aggregate route checks"
    )
    assert "GITHUB_EVENT_NAME" not in aggregate_script
    assert "--scheduled" not in aggregate_script
