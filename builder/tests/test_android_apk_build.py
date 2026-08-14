from __future__ import annotations

import importlib.util
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "android" / "build_game_apk.py"
SPEC = importlib.util.spec_from_file_location("build_game_apk", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

ANDROID_NS = "http://schemas.android.com/apk/res/android"
A = f"{{{ANDROID_NS}}}"


def test_patch_manifest_moves_launcher_to_bootstrap(tmp_path: Path) -> None:
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text(
        f'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="{ANDROID_NS}"
    package="com.feimo.astralpartyjpn"
    android:versionName="3.2.0">
  <uses-permission android:name="android.permission.INTERNET" />
  <application android:appComponentFactory="org.lsposed.lspatch.metaloader.LSPAppComponentFactory">
    <meta-data android:name="lspatch" android:value="preserve-me" />
    <activity
        android:name="com.femoo.sdk.Femoo_UnityActivity"
        android:exported="true"
        android:screenOrientation="11">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity>
  </application>
</manifest>
''',
        encoding="utf-8",
    )

    game_activity, version = MODULE.patch_manifest(manifest)
    assert game_activity == "com.femoo.sdk.Femoo_UnityActivity"
    assert version == "3.2.0"

    root = ET.parse(manifest).getroot()
    application = root.find("application")
    assert application is not None
    assert (
        application.get(A + "appComponentFactory")
        == "org.lsposed.lspatch.metaloader.LSPAppComponentFactory"
    )
    metadata = {item.get(A + "name"): item for item in application.findall("meta-data")}
    assert metadata["lspatch"].get(A + "value") == "preserve-me"
    activities = {item.get(A + "name"): item for item in application.findall("activity")}
    bootstrap = activities[MODULE.BOOTSTRAP_ACTIVITY]
    original = activities["com.femoo.sdk.Femoo_UnityActivity"]
    assert bootstrap.get(A + "screenOrientation") == "11"
    assert not any(MODULE.intent_is_launcher(item) for item in original.findall("intent-filter"))
    assert any(MODULE.intent_is_launcher(item) for item in bootstrap.findall("intent-filter"))


def test_assemble_unsigned_apk_replaces_only_owned_entries(tmp_path: Path) -> None:
    base = tmp_path / "base.apk"
    with zipfile.ZipFile(base, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"old-manifest")
        archive.writestr(MODULE.DATA_UNITY_PATH, b"old-unity")
        archive.writestr("classes.dex", b"game-dex")
        archive.writestr("assets/original.dat", b"keep-me")
        archive.writestr("META-INF/MANIFEST.MF", b"old-signature-manifest")
        archive.writestr("META-INF/CERT.RSA", b"old-signature")

    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_bytes(b"new-manifest")
    unity = tmp_path / "data.unity3d"
    unity.write_bytes(b"new-unity")
    runtime = tmp_path / "runtime.dex"
    runtime.write_bytes(b"runtime-dex")
    output = tmp_path / "unsigned.apk"
    config = {
        "schemaVersion": 1,
        "route": "INT_ANDROID",
        "channel": "release",
    }

    MODULE.assemble_unsigned_apk(base, manifest, unity, runtime, config, output)

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert archive.read("AndroidManifest.xml") == b"new-manifest"
        assert archive.read(MODULE.DATA_UNITY_PATH) == b"new-unity"
        assert archive.read("classes.dex") == b"game-dex"
        assert archive.read("classes2.dex") == b"runtime-dex"
        assert archive.read("assets/original.dat") == b"keep-me"
        assert json.loads(archive.read("assets/astralpatch/config.json")) == config
        assert "META-INF/MANIFEST.MF" not in names
        assert "META-INF/CERT.RSA" not in names
