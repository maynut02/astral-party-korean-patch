import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _manual_patch_arguments(job_name: str) -> dict[str, str]:
    jobs = yaml.safe_load((ROOT / ".github/workflows/patch.yml").read_text(encoding="utf-8"))[
        "jobs"
    ]
    if job_name == "build-mobile":
        job = jobs.get("build-mobile") or jobs["build-android"]
    else:
        job = jobs[job_name]
    marker = "python tools/workflow/manual_patch.py"
    script = next(step["run"] for step in job["steps"] if marker in step.get("run", ""))
    command = []
    for line in script[script.index(marker) :].splitlines():
        command.append(line.rstrip().removesuffix("\\"))
        if not line.rstrip().endswith("\\"):
            break
    tokens = shlex.split(" ".join(command))
    assert tokens[:2] == ["python", "tools/workflow/manual_patch.py"]
    return dict(zip(tokens[2::2], tokens[3::2], strict=True))


def test_steam_manual_zip_uses_the_builds_versioned_catalog() -> None:
    assert _manual_patch_arguments("build-steam") == {
        "--manifest": "$root/manifest.json",
        "--payloads-dir": "$work/payloads",
        "--catalog": "$work/catalog/catalog_${{ needs.plan.outputs.game_version }}.json",
        "--output": "output/release/${{ matrix.route }}_manual_patch.zip",
    }


def test_mobile_manual_zip_command_remains_without_catalog() -> None:
    assert _manual_patch_arguments("build-mobile") == {
        "--manifest": "$root/manifest.json",
        "--payloads-dir": "$work/payloads",
        "--output": "output/release/${{ matrix.route }}_manual_patch.zip",
    }
