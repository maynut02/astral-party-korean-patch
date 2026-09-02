import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/workflow/patch_plan.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("astral_patch_plan_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _check(module, route, revision, *, changed=False, sync=False):
    return module.RouteCheck(
        route=route,
        game_version="3.2.0",
        revision=revision,
        revision_id=f"id-{route}" if not sync else "",
        changed=changed,
        sync_required=sync,
    )


def test_plan_uses_highest_route_revision_and_reports_changed_routes() -> None:
    module = _load_module()
    checks = (
        _check(module, "INT_STEAM", "116"),
        _check(module, "CN_STEAM", "116"),
        _check(module, "INT_ANDROID", "117", changed=True, sync=True),
    )
    plan = module.build_plan(checks)
    assert plan["should_run"] is True
    assert plan["max_revision"] == "117"
    assert plan["updated_routes"] == ("INT_ANDROID",)
    assert plan["int_android_sync_required"] is True


def test_manual_run_still_publishes_when_every_route_is_current() -> None:
    module = _load_module()
    checks = tuple(_check(module, route, "116") for route in module.ROUTES)
    plan = module.build_plan(checks)
    assert plan["should_run"] is True
    assert plan["updated_routes"] == ()


def test_revision_sort_is_natural() -> None:
    module = _load_module()
    assert max(("99", "100", "9"), key=module.revision_key) == "100"
    assert max(("r9", "r10"), key=module.revision_key) == "r10"


def test_immutable_release_tag_starts_at_p0_and_increments() -> None:
    spec = importlib.util.spec_from_file_location(
        "astral_release_tag_test", ROOT / "tools/workflow/release_tag.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    base = "v3.2.0_r117"
    assert module.next_immutable_tag(base, ()) == f"{base}_p0"
    assert module.next_immutable_tag(base, (f"{base}_p0",)) == f"{base}_p1"
    assert module.next_immutable_tag(
        base, (f"{base}_p0", f"{base}_p2", f"{base}_p4")
    ) == f"{base}_p5"
    # Existing legacy unsuffixed tags are treated as p0 for migration purposes.
    assert module.next_immutable_tag(base, (base,)) == f"{base}_p1"
    assert module.next_immutable_tag(base, ("v3.2.0_r1170", f"{base}_preview")) == f"{base}_p0"
