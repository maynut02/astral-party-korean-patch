from __future__ import annotations

import importlib.util
import json
import struct
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


def test_parse_badging_version_name_reads_real_package_version() -> None:
    output = (
        "package: name='com.feimo.astralpartyjpn' versionCode='555' "
        "versionName='3.2.0' compileSdkVersion='35'\n"
    )
    assert MODULE.parse_badging_version_name(output) == "3.2.0"


def test_parse_badging_version_name_rejects_missing_version() -> None:
    try:
        MODULE.parse_badging_version_name("package: name='com.feimo.astralpartyjpn'\n")
    except RuntimeError as exc:
        assert "does not contain versionName" in str(exc)
    else:
        raise AssertionError("missing versionName should fail")


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



def _fake_signing_block(payload: bytes = b"test-signature-payload") -> bytes:
    size_without_first = len(payload) + 24
    return (
        struct.pack("<Q", size_without_first)
        + payload
        + struct.pack("<Q", size_without_first)
        + MODULE.APK_SIG_BLOCK_MAGIC
    )


def test_signing_block_bridge_preserves_zip_and_block(tmp_path: Path) -> None:
    plain = tmp_path / "plain.apk"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("classes.dex", b"dex")

    block = _fake_signing_block()
    bridged = tmp_path / "bridged.apk"
    MODULE.inject_apk_signing_block(plain, bridged, block)

    assert MODULE.extract_apk_signing_block(bridged) == block
    with zipfile.ZipFile(bridged, "r") as archive:
        assert archive.read("AndroidManifest.xml") == b"manifest"
        assert archive.read("classes.dex") == b"dex"
        assert archive.testzip() is None


def test_signing_block_bridge_rejects_unsigned_apk(tmp_path: Path) -> None:
    plain = tmp_path / "plain.apk"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("classes.dex", b"dex")

    try:
        MODULE.extract_apk_signing_block(plain)
    except RuntimeError as exc:
        assert "no v2/v3 signing block" in str(exc)
    else:
        raise AssertionError("unsigned APK should not expose a signing block")


def test_prepare_lspatch_input_aligns_before_inserting_play_block(
    tmp_path: Path, monkeypatch
) -> None:
    original = tmp_path / "play.apk"
    original_plain = tmp_path / "play-plain.apk"
    with zipfile.ZipFile(original_plain, "w") as archive:
        archive.writestr("classes.dex", b"play")
    play_block = _fake_signing_block(b"play-certificate")
    MODULE.inject_apk_signing_block(original_plain, original, play_block)

    unsigned = tmp_path / "modified.apk"
    with zipfile.ZipFile(unsigned, "w") as archive:
        archive.writestr("classes.dex", b"modified")
    output = tmp_path / "prepared.apk"
    zipalign = tmp_path / "zipalign"
    calls: list[list[str]] = []

    monkeypatch.setattr(
        MODULE,
        "android_tools",
        lambda _sdk: (
            tmp_path / "android.jar",
            tmp_path / "d8",
            zipalign,
            tmp_path / "apksigner",
        ),
    )

    def fake_run(args: list[str], **_kwargs: object) -> None:
        calls.append(args)
        assert args[0] == str(zipalign)
        Path(args[-1]).write_bytes(unsigned.read_bytes())

    monkeypatch.setattr(MODULE, "run", fake_run)
    MODULE.prepare_lspatch_input(unsigned, output, tmp_path, original)

    assert calls == [
        [str(zipalign), "-f", "-p", "4", str(unsigned), str(output.with_suffix(".aligned.apk"))]
    ]
    assert MODULE.extract_apk_signing_block(output) == play_block
    with zipfile.ZipFile(output, "r") as archive:
        assert archive.read("classes.dex") == b"modified"


def test_runtime_config_separates_catalog_and_assetbundle_caches() -> None:
    config = MODULE.build_runtime_config(
        "https://raw.githubusercontent.com/example/repo/distribution/release-index.json",
        "com.femoo.sdk.Femoo_UnityActivity",
    )
    assert config["addressablesDir"] == "com.unity.addressables"
    assert config["assetBundleCacheDir"] == "com.unity.addressables/AssetBundles"


def test_android_runtime_does_not_compare_cached_bundle_to_cdn_source_bytes() -> None:
    engine = (ROOT / "android/runtime/src/com/astralpatch/runtime/PatchEngine.java").read_text(
        encoding="utf-8"
    )
    runtime_config = (
        ROOT / "android/runtime/src/com/astralpatch/runtime/RuntimeConfig.java"
    ).read_text(encoding="utf-8")

    assert '"com.unity.addressables/AssetBundles"' in runtime_config
    assert "assetBundleCacheRoot(context, config)" in engine
    assert "firstMissingBundlePath(bundleRoot, manifest.files)" in engine
    assert 'verifyFile(source, item.sourceSize, item.sourceSha256, "source")' not in engine
    assert "sourceHash = sha256(source)" in engine
    bootstrap = (
        ROOT / "android/runtime/src/com/astralpatch/runtime/BootstrapActivity.java"
    ).read_text(encoding="utf-8")
    assert "대기 중인 리소스: " in bootstrap
