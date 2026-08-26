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


def test_patch_workflow_keeps_only_pre_release_mutable() -> None:
    jobs = _workflow()["jobs"]
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
    assert "tag=patch-pre" in plan_script
    assert "tools/workflow/release_tag.py" in plan_script
    assert 'if [ "$MODE" = "pre" ]' in publish_script
    assert "immutable release tag already exists" in publish_script


def test_patch_workflow_has_no_github_cron_and_pre_is_change_driven() -> None:
    workflow = _workflow()
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert "schedule" not in triggers
    assert "workflow_dispatch" in triggers
    jobs = workflow["jobs"]
    aggregate_script = next(
        step["run"]
        for step in jobs["plan"]["steps"]
        if step.get("name") == "Aggregate route checks"
    )
    assert "GITHUB_EVENT_NAME" not in aggregate_script
    assert "--scheduled" not in aggregate_script
