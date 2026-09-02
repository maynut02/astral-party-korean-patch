from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/patch.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_patch_workflow_is_split_into_parallel_roles() -> None:
    jobs = _workflow()["jobs"]
    assert set(jobs) == {
        "source",
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
        "notify",
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
    assert triggers["workflow_dispatch"] is None
    assert triggers["repository_dispatch"]["types"] == ["patch-watcher"]

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


def test_patch_workflow_uses_one_unified_original_release() -> None:
    jobs = _workflow()["jobs"]
    plan_script = next(
        step["run"]
        for step in jobs["plan"]["steps"]
        if step.get("name") == "Define immutable release identity"
    )
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
        if step.get("name") == "Publish or verify unified original release"
    )

    assert 'original_tag="${base}-original"' in plan_script
    assert 'if [ "$tag" = "${base}_p0" ]; then' in plan_script
    assert "original_mode=unified" in plan_script
    assert "original_mode=legacy" in plan_script
    assert "publish_original=true" in plan_script
    assert "publish_original=false" in plan_script
    assert '${base}_p*-original' not in plan_script
    assert "--source-asset-base-url" in steam_build
    assert "--source-asset-base-url" in android_build
    assert "needs.plan.outputs.original_tag" in steam_build
    assert "needs.plan.outputs.original_tag" in android_build
    assert "needs.plan.outputs.original_mode" in steam_build
    assert "needs.plan.outputs.original_mode" in android_build
    assert 'source_tag="original-${{ matrix.route }}-' in steam_build
    assert 'source_tag="original-INT_ANDROID-' in android_build
    assert "for route in INT_STEAM CN_STEAM INT_ANDROID" in original_publish
    assert 'if [ "$ORIGINAL_MODE" = legacy ]; then' in original_publish
    assert "reusing legacy per-route original releases" in original_publish
    assert 'gh release create "$ORIGINAL_TAG"' in original_publish
    assert "verify_original_release" in original_publish
    assert "earlier pipeline attempt" in original_publish
    assert "reusing unified original release" in original_publish
    assert "immutable original release is incomplete" in original_publish


def test_patch_workflow_separates_watcher_and_manual_sources() -> None:
    workflow = _workflow()
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert "schedule" not in triggers
    assert triggers["workflow_dispatch"] is None
    assert triggers["repository_dispatch"]["types"] == ["patch-watcher"]
    assert "GAME_VERSION" not in workflow.get("env", {})

    jobs = workflow["jobs"]
    source_script = next(
        step["run"]
        for step in jobs["source"]["steps"]
        if step.get("name") == "Resolve automatic or manual source"
    )
    assert "WATCHER_GAME_VERSION" in source_script
    assert 'gh release list --repo "$GITHUB_REPOSITORY"' in source_script
    assert "manual patch requires an existing patch release" in source_script

    check_steps = jobs["check"]["steps"]
    automatic_check = next(
        step for step in check_steps if step.get("name") == "Check remote revision and catalog"
    )
    manual_reuse = next(
        step for step in check_steps if step.get("name") == "Reuse latest released revision"
    )
    assert "needs.source.outputs.game_version" in automatic_check["run"]
    assert "astral-builder reuse-release" in manual_reuse["run"]
    assert "needs.source.outputs.automatic != 'true'" == manual_reuse["if"]

    plan_script = next(
        step["run"]
        for step in jobs["plan"]["steps"]
        if step.get("name") == "Define immutable release identity"
    )
    assert "manual patch cannot create a new p0 release" in plan_script


def test_patch_workflow_notifies_discord_after_successful_finalize() -> None:
    jobs = _workflow()["jobs"]
    notify = jobs["notify"]
    assert set(notify["needs"]) == {"plan", "state", "finalize"}
    assert "needs.finalize.result == 'success'" in notify["if"]

    step = next(
        item for item in notify["steps"] if item.get("name") == "Send release notification"
    )
    assert step["continue-on-error"] is True
    assert step["env"]["DISCORD_WEBHOOK"] == "${{ secrets.DISCORD_WEBHOOK }}"
    assert "tools/workflow/notify_discord.py" in step["run"]
    assert "needs.plan.outputs.tag" in step["run"]
    assert "needs.plan.outputs.updated_routes_csv" in step["run"]
    assert "needs.state.outputs.int_steam_revision" in step["run"]
    assert "needs.state.outputs.cn_steam_revision" in step["run"]
    assert "needs.state.outputs.int_android_revision" in step["run"]
