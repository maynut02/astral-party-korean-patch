from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _jobs():
    return yaml.safe_load((ROOT / ".github/workflows/patch.yml").read_text(encoding="utf-8"))[
        "jobs"
    ]


def test_steam_plan_uses_synchronized_revision_ids_and_downloads_only_planned_files():
    job = _jobs()["steam-inputs"]
    assert set(job["needs"]) == {"plan", "state"}
    plan = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Plan catalog-driven Steam inputs"
    )
    download = next(
        step["run"] for step in job["steps"] if step.get("name") == "Download planned Steam files"
    )
    for route in ("int_steam", "cn_steam"):
        assert f"needs.state.outputs.{route}_revision_id" in plan
        assert f"--route-config routes/{route}.yaml" in plan
        assert f"--filelist .work/steam-plan/{route}.files" in plan
    assert "-filelist .work/steam-filelist.txt" in download
    assert ".bundle" not in download  # Filenames must come from this revision's catalog.
    assert any(
        step.get("uses") == "./.github/actions/setup-database-tunnel" for step in job["steps"]
    )


def test_steam_artifacts_retain_streaming_assets_and_reach_builder():
    jobs = _jobs()
    uploads = {
        step["with"]["name"]: step["with"]["path"]
        for step in jobs["steam-inputs"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    }
    for route in jobs["build-steam"]["strategy"]["matrix"]["include"]:
        assert uploads[route["legacy_artifact"]].rstrip("/") == route["legacy_dir"]
    build = next(
        step["run"]
        for step in jobs["build-steam"]["steps"]
        if step.get("name") == "Build and validate"
    )
    assert "--steam-data-dir '${{ matrix.legacy_dir }}'" in build
    mobile = jobs["build-android"]
    for step in mobile["steps"]:
        assert "--steam-data-dir" not in step.get("run", "")
        assert "astral-builder steam-inputs" not in step.get("run", "")


def test_workflow_dependencies_remain_acyclic():
    jobs = _jobs()
    complete = set()

    def visit(name, active):
        assert name not in active, f"workflow dependency cycle at {name}"
        if name in complete:
            return
        needs = jobs[name].get("needs", [])
        for dependency in [needs] if isinstance(needs, str) else needs:
            visit(dependency, active | {name})
        complete.add(name)

    for name in jobs:
        visit(name, set())
