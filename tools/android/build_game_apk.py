from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_SRC = ROOT / "builder" / "src"
if str(BUILDER_SRC) not in sys.path:
    sys.path.insert(0, str(BUILDER_SRC))

from astral_builder.patch.fonts import patch_legacy_font

ANDROID_NS = "http://schemas.android.com/apk/res/android"
A = f"{{{ANDROID_NS}}}"
BOOTSTRAP_ACTIVITY = "com.astralpatch.runtime.BootstrapActivity"
DEFAULT_GAME_ACTIVITY = "com.femoo.sdk.Femoo_UnityActivity"
DATA_UNITY_PATH = "assets/bin/Data/data.unity3d"


def run(
    args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=cwd, env=env, check=True)


def executable(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise RuntimeError(f"required executable was not found: {name}")
    return value


def version_key(path: Path) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in path.name.replace("-rc", ".").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def discover_android_sdk() -> Path:
    for name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        raw = os.environ.get(name)
        if raw and Path(raw).is_dir():
            return Path(raw)
    raise RuntimeError("ANDROID_SDK_ROOT or ANDROID_HOME must point to an Android SDK")


def latest_child(root: Path) -> Path:
    candidates = [item for item in root.iterdir() if item.is_dir()]
    if not candidates:
        raise RuntimeError(f"no Android SDK versions found under {root}")
    return max(candidates, key=version_key)


def android_tools(sdk: Path) -> tuple[Path, Path, Path, Path]:
    platform = latest_child(sdk / "platforms")
    build_tools = latest_child(sdk / "build-tools")
    android_jar = platform / "android.jar"
    d8 = build_tools / ("d8.bat" if os.name == "nt" else "d8")
    zipalign = build_tools / ("zipalign.exe" if os.name == "nt" else "zipalign")
    apksigner = build_tools / ("apksigner.bat" if os.name == "nt" else "apksigner")
    for item in (android_jar, d8, zipalign, apksigner):
        if not item.exists():
            raise RuntimeError(f"Android SDK tool is missing: {item}")
    return android_jar, d8, zipalign, apksigner


def intent_is_launcher(intent: ET.Element) -> bool:
    actions = {child.get(A + "name") for child in intent.findall("action")}
    categories = {child.get(A + "name") for child in intent.findall("category")}
    return (
        "android.intent.action.MAIN" in actions
        and "android.intent.category.LAUNCHER" in categories
    )


def patch_manifest(path: Path) -> tuple[str, str]:
    ET.register_namespace("android", ANDROID_NS)
    tree = ET.parse(path)
    root = tree.getroot()
    package = root.get("package")
    if not package:
        raise RuntimeError("decoded AndroidManifest.xml has no package")
    if package != "com.feimo.astralpartyjpn":
        raise RuntimeError(f"unexpected Android package: {package}")
    application = root.find("application")
    if application is None:
        raise RuntimeError("decoded AndroidManifest.xml has no application")
    if any(
        activity.get(A + "name") == BOOTSTRAP_ACTIVITY
        for activity in application.findall("activity")
    ):
        raise RuntimeError(
            "base APK already contains AstralPatchRuntime bootstrap Activity"
        )

    launchers: list[tuple[ET.Element, ET.Element]] = []
    for activity in application.findall("activity"):
        for intent in list(activity.findall("intent-filter")):
            if intent_is_launcher(intent):
                launchers.append((activity, intent))
    if len(launchers) != 1:
        raise RuntimeError(
            f"expected exactly one launcher Activity, found {len(launchers)}"
        )

    original_activity, launcher_filter = launchers[0]
    game_activity = original_activity.get(A + "name")
    if not game_activity:
        raise RuntimeError("launcher Activity has no android:name")
    original_activity.remove(launcher_filter)

    bootstrap_attrs = {A + "name": BOOTSTRAP_ACTIVITY, A + "exported": "true"}
    for key in (
        "theme",
        "screenOrientation",
        "configChanges",
        "hardwareAccelerated",
        "resizeableActivity",
    ):
        value = original_activity.get(A + key)
        if value is not None:
            bootstrap_attrs[A + key] = value
    bootstrap = ET.SubElement(application, "activity", bootstrap_attrs)
    intent = ET.SubElement(bootstrap, "intent-filter")
    ET.SubElement(intent, "action", {A + "name": "android.intent.action.MAIN"})
    ET.SubElement(intent, "category", {A + "name": "android.intent.category.LAUNCHER"})

    permissions = {item.get(A + "name") for item in root.findall("uses-permission")}
    if "android.permission.INTERNET" not in permissions:
        ET.SubElement(
            root, "uses-permission", {A + "name": "android.permission.INTERNET"}
        )

    version_name = root.get(A + "versionName", "unknown")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return game_activity, version_name


def compile_runtime(runtime_src: Path, work: Path, sdk: Path) -> Path:
    android_jar, d8, _, _ = android_tools(sdk)
    classes = work / "runtime-classes"
    dex_out = work / "runtime-dex"
    classes.mkdir(parents=True)
    dex_out.mkdir(parents=True)
    sources = sorted(str(path) for path in runtime_src.rglob("*.java"))
    if not sources:
        raise RuntimeError(f"no Java runtime sources found under {runtime_src}")
    run(
        [
            executable("javac"),
            "-encoding",
            "UTF-8",
            "-source",
            "8",
            "-target",
            "8",
            "-classpath",
            str(android_jar),
            "-d",
            str(classes),
            *sources,
        ]
    )
    class_files = sorted(str(path) for path in classes.rglob("*.class"))
    run(
        [
            str(d8),
            "--min-api",
            "23",
            "--lib",
            str(android_jar),
            "--output",
            str(dex_out),
            *class_files,
        ]
    )
    dex = dex_out / "classes.dex"
    if not dex.is_file():
        raise RuntimeError("d8 did not produce classes.dex")
    return dex


def next_dex_name(apk: Path) -> str:
    highest = 0
    with zipfile.ZipFile(apk) as archive:
        for name in archive.namelist():
            if name == "classes.dex":
                highest = max(highest, 1)
            elif name.startswith("classes") and name.endswith(".dex"):
                middle = name[len("classes") : -len(".dex")]
                if middle.isdigit():
                    highest = max(highest, int(middle))
    return f"classes{highest + 1}.dex" if highest else "classes.dex"


def extract_apk_member(apk: Path, member: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk, "r") as archive:
        try:
            info = archive.getinfo(member)
        except KeyError as exc:
            raise RuntimeError(f"base APK does not contain {member}") from exc
        with archive.open(info, "r") as source, output.open("wb") as target:
            shutil.copyfileobj(source, target, 1024 * 1024)


def compile_manifest(base_apk: Path, work: Path, apktool: str) -> tuple[Path, str, str]:
    project = work / "manifest-project"
    rebuilt = work / "manifest-rebuilt.apk"
    compiled = work / "AndroidManifest.xml.binary"
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(work / "xdg-data")
    run(
        [
            apktool,
            "decode",
            "--no-src",
            "--no-assets",
            "--force",
            str(base_apk),
            "--output",
            str(project),
        ],
        env=env,
    )
    game_activity, version_name = patch_manifest(project / "AndroidManifest.xml")
    run([apktool, "build", str(project), "--output", str(rebuilt)], env=env)
    extract_apk_member(rebuilt, "AndroidManifest.xml", compiled)
    return compiled, game_activity, version_name


def _copy_zip_stream(
    source_archive: zipfile.ZipFile,
    target_archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> None:
    with (
        source_archive.open(info, "r") as source,
        target_archive.open(info, "w", force_zip64=True) as target,
    ):
        shutil.copyfileobj(source, target, 1024 * 1024)


def _write_zip_file(
    target_archive: zipfile.ZipFile, info: zipfile.ZipInfo, source_path: Path
) -> None:
    with (
        source_path.open("rb") as source,
        target_archive.open(info, "w", force_zip64=True) as target,
    ):
        shutil.copyfileobj(source, target, 1024 * 1024)


def assemble_unsigned_apk(
    base_apk: Path,
    compiled_manifest: Path,
    patched_unity: Path,
    runtime_dex: Path,
    config: dict[str, object],
    output: Path,
) -> None:
    dex_name = next_dex_name(base_apk)
    signature_suffixes = (".RSA", ".DSA", ".EC", ".SF")
    replacements = {
        "AndroidManifest.xml": compiled_manifest,
        DATA_UNITY_PATH: patched_unity,
    }
    seen_replacements: set[str] = set()
    with (
        zipfile.ZipFile(base_apk, "r") as source,
        zipfile.ZipFile(output, "w", allowZip64=True) as target,
    ):
        for info in source.infolist():
            upper = info.filename.upper()
            if upper == "META-INF/MANIFEST.MF" or (
                upper.startswith("META-INF/") and upper.endswith(signature_suffixes)
            ):
                continue
            if info.filename in {dex_name, "assets/astralpatch/config.json"}:
                continue
            replacement = replacements.get(info.filename)
            if replacement is not None:
                _write_zip_file(target, info, replacement)
                seen_replacements.add(info.filename)
            else:
                _copy_zip_stream(source, target, info)
        missing = set(replacements) - seen_replacements
        if missing:
            raise RuntimeError(
                f"base APK is missing required entries: {sorted(missing)}"
            )
        target.write(runtime_dex, dex_name, compress_type=zipfile.ZIP_DEFLATED)
        target.writestr(
            "assets/astralpatch/config.json",
            json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )


def sign_apk(
    unsigned: Path,
    output: Path,
    sdk: Path,
    keystore: Path,
    alias: str,
    ks_pass_env: str,
    key_pass_env: str,
) -> None:
    _, _, zipalign, apksigner = android_tools(sdk)
    aligned = output.with_suffix(".aligned.apk")
    run([str(zipalign), "-f", "-p", "4", str(unsigned), str(aligned)])
    env = os.environ.copy()
    if ks_pass_env not in env:
        raise RuntimeError(
            f"missing signing password environment variable: {ks_pass_env}"
        )
    if key_pass_env not in env:
        raise RuntimeError(
            f"missing signing password environment variable: {key_pass_env}"
        )
    run(
        [
            str(apksigner),
            "sign",
            "--ks",
            str(keystore),
            "--ks-key-alias",
            alias,
            "--ks-pass",
            f"env:{ks_pass_env}",
            "--key-pass",
            f"env:{key_pass_env}",
            "--out",
            str(output),
            str(aligned),
        ],
        env=env,
    )
    run([str(apksigner), "verify", "--verbose", "--print-certs", str(output)], env=env)
    aligned.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a self-patching INT_ANDROID Astral Party APK from a merged base APK."
    )
    parser.add_argument("--base-apk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--release-index-url",
        required=True,
        help="HTTPS release-index.json URL used by the embedded Addressables runtime",
    )
    parser.add_argument(
        "--font-file",
        type=Path,
        default=ROOT / "resources" / "int_android" / "legacy-font.ttf",
    )
    parser.add_argument("--font-name", default="MochiyPopOne-Regular")
    parser.add_argument("--keystore", type=Path, required=True)
    parser.add_argument("--key-alias", required=True)
    parser.add_argument("--ks-pass-env", default="ASTRAL_ANDROID_KEYSTORE_PASSWORD")
    parser.add_argument("--key-pass-env", default="ASTRAL_ANDROID_KEY_PASSWORD")
    parser.add_argument("--apktool", default="apktool")
    parser.add_argument("--android-sdk", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.base_apk.is_file():
        raise RuntimeError(f"base APK not found: {args.base_apk}")
    if not args.font_file.is_file():
        raise RuntimeError(f"Korean legacy font not found: {args.font_file}")
    if not args.keystore.is_file():
        raise RuntimeError(f"Android signing keystore not found: {args.keystore}")
    if not args.release_index_url.startswith("https://"):
        raise RuntimeError("release index URL must use HTTPS")

    sdk = args.android_sdk or discover_android_sdk()
    apktool = shutil.which(args.apktool) or args.apktool
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    temp_parent = ROOT / ".work" / "android-apk"
    temp_parent.mkdir(parents=True, exist_ok=True)
    if args.keep_work:
        work = temp_parent / "current"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        cleanup = False
    else:
        work = Path(tempfile.mkdtemp(prefix="build-", dir=temp_parent))
        cleanup = True

    try:
        base_apk = args.base_apk.resolve()
        compiled_manifest, game_activity, version_name = compile_manifest(
            base_apk, work, str(apktool)
        )
        if game_activity != DEFAULT_GAME_ACTIVITY:
            print(
                f"warning: detected game launcher is {game_activity}, expected {DEFAULT_GAME_ACTIVITY}"
            )

        original_unity = work / "data.unity3d.original"
        extract_apk_member(base_apk, DATA_UNITY_PATH, original_unity)
        patched_unity = work / "data.unity3d.patched"
        patch_legacy_font(
            original_unity,
            patched_unity,
            font_name=args.font_name,
            font_payload=args.font_file.read_bytes(),
        )

        runtime_dex = compile_runtime(ROOT / "android" / "runtime" / "src", work, sdk)
        injected = work / "runtime-injected.apk"
        assemble_unsigned_apk(
            base_apk,
            compiled_manifest,
            patched_unity,
            runtime_dex,
            {
                "schemaVersion": 1,
                "route": "INT_ANDROID",
                "channel": "release",
                "releaseIndexUrl": args.release_index_url,
                "gameActivity": game_activity,
                "addressablesDir": "com.unity.addressables",
                "watcherIntervalSeconds": 30,
            },
            injected,
        )
        sign_apk(
            injected,
            output,
            sdk,
            args.keystore.resolve(),
            args.key_alias,
            args.ks_pass_env,
            args.key_pass_env,
        )
        metadata = {
            "package": "com.feimo.astralpartyjpn",
            "baseVersionName": version_name,
            "gameActivity": game_activity,
            "output": str(output),
        }
        output.with_suffix(output.suffix + ".json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    finally:
        if cleanup:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
