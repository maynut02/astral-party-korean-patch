from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/android/prepare_original_apks.py"


def _module():
    spec = importlib.util.spec_from_file_location("prepare_original_apks", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_prepare_preserves_apk_bytes_and_writes_release_metadata(tmp_path, monkeypatch) -> None:
    module = _module()
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    base = input_dir / "downloaded-base.apk"
    split = input_dir / "downloaded-arm64.apk"
    base.write_bytes(b"base-google-play-apk")
    split.write_bytes(b"split-google-play-apk")

    def inspect(path, _aapt, _apksigner):
        split_name = None if path == base else "config.arm64_v8a"
        return module.ApkMetadata(
            source=path,
            package_name=module.PACKAGE_NAME,
            version_name="1.2.3",
            version_code=1234,
            split_name=split_name,
            certificate_sha256="a" * 64,
            sha256=module._sha256(path),
            size=path.stat().st_size,
        )

    monkeypatch.setattr(module, "_inspect", inspect)
    payload = module.prepare(input_dir, output_dir, "aapt", "apksigner", "px_9a")

    assert (output_dir / "base.apk").read_bytes() == base.read_bytes()
    assert (output_dir / "split-001-config.arm64_v8a.apk").read_bytes() == split.read_bytes()
    assert payload["releaseTag"] == "android-game-v1234"
    assert payload["certificateSha256"] == "a" * 64
    written = json.loads((output_dir / "AstralPartyOriginal.json").read_text(encoding="utf-8"))
    assert written == payload


def test_original_apk_workflow_never_merges_or_signs_game_apks() -> None:
    workflow_path = ROOT / ".github/workflows/android-game-original.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(workflow)
    assert "release" in parsed["jobs"]
    assert "split_apk=true" in workflow
    assert "prepare_original_apks.py" in workflow
    assert "APKEditor" not in workflow
    assert "apksigner sign" not in workflow
    assert "install-multiple" not in workflow
