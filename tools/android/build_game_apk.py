from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
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
RESTART_REQUIRED_ACTIVITY = "com.astralpatch.runtime.RestartRequiredActivity"
DEFAULT_GAME_ACTIVITY = "com.femoo.sdk.Femoo_UnityActivity"
DATA_UNITY_PATH = "assets/bin/Data/data.unity3d"
APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
ZIP_EOCD_MIN_SIZE = 22
ZIP_MAX_COMMENT = 0xFFFF


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


def parse_badging_version_name(text: str) -> str:
    match = re.search(r"(?:^|\s)versionName='([^']+)'", text, re.MULTILINE)
    if not match:
        raise RuntimeError("aapt2 badging output does not contain versionName")
    value = match.group(1).strip()
    if not value or value.lower() == "unknown":
        raise RuntimeError(f"invalid APK versionName from aapt2: {value!r}")
    return value


def read_apk_version_name(apk: Path, sdk: Path) -> str:
    build_tools = latest_child(sdk / "build-tools")
    aapt2 = build_tools / ("aapt2.exe" if os.name == "nt" else "aapt2")
    if not aapt2.exists():
        raise RuntimeError(f"Android SDK tool is missing: {aapt2}")
    result = subprocess.run(
        [str(aapt2), "dump", "badging", str(apk)],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_badging_version_name(result.stdout)


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
    runtime_activities = {
        activity.get(A + "name") for activity in application.findall("activity")
    }
    if (
        BOOTSTRAP_ACTIVITY in runtime_activities
        or RESTART_REQUIRED_ACTIVITY in runtime_activities
    ):
        raise RuntimeError("base APK already contains AstralPatchRuntime Activity")

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

    restart_attrs = {
        A + "name": RESTART_REQUIRED_ACTIVITY,
        A + "exported": "false",
    }
    for key in (
        "theme",
        "screenOrientation",
        "configChanges",
        "hardwareAccelerated",
        "resizeableActivity",
    ):
        value = original_activity.get(A + key)
        if value is not None:
            restart_attrs[A + key] = value
    ET.SubElement(application, "activity", restart_attrs)

    permissions = {item.get(A + "name") for item in root.findall("uses-permission")}
    if "android.permission.INTERNET" not in permissions:
        ET.SubElement(
            root, "uses-permission", {A + "name": "android.permission.INTERNET"}
        )

    version_name = root.get(A + "versionName", "unknown")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return game_activity, version_name


def build_runtime_config(
    release_index_url: str, game_activity: str
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "route": "INT_ANDROID",
        "channel": "release",
        "releaseIndexUrl": release_index_url,
        "gameActivity": game_activity,
        "addressablesDir": "com.unity.addressables",
        "assetBundleCacheDir": "com.unity.addressables/AssetBundles",
        "watcherIntervalSeconds": 30,
    }


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


def _find_eocd(apk: Path) -> tuple[int, int]:
    size = apk.stat().st_size
    tail_size = min(size, ZIP_EOCD_MIN_SIZE + ZIP_MAX_COMMENT)
    with apk.open("rb") as handle:
        handle.seek(size - tail_size)
        tail = handle.read(tail_size)
    search_end = len(tail)
    while True:
        position = tail.rfind(ZIP_EOCD_SIGNATURE, 0, search_end)
        if position < 0:
            raise RuntimeError(f"APK has no valid ZIP EOCD: {apk}")
        if position + ZIP_EOCD_MIN_SIZE <= len(tail):
            comment_length = struct.unpack_from("<H", tail, position + 20)[0]
            if position + ZIP_EOCD_MIN_SIZE + comment_length == len(tail):
                eocd_offset = size - tail_size + position
                central_directory_offset = struct.unpack_from(
                    "<I", tail, position + 16
                )[0]
                if central_directory_offset == 0xFFFFFFFF:
                    raise RuntimeError(
                        "ZIP64 APKs are not supported by the signing-block bridge"
                    )
                return eocd_offset, central_directory_offset
        search_end = position


def extract_apk_signing_block(apk: Path) -> bytes:
    _, central_directory_offset = _find_eocd(apk)
    if central_directory_offset < 32:
        raise RuntimeError(f"APK has no v2/v3 signing block: {apk}")
    with apk.open("rb") as handle:
        handle.seek(central_directory_offset - 24)
        footer = handle.read(24)
        if len(footer) != 24 or footer[8:] != APK_SIG_BLOCK_MAGIC:
            raise RuntimeError(f"APK has no v2/v3 signing block: {apk}")
        block_size_without_first_size = struct.unpack_from("<Q", footer, 0)[0]
        block_size = block_size_without_first_size + 8
        if block_size > central_directory_offset or block_size < 32:
            raise RuntimeError("APK signing block has an invalid size")
        block_start = central_directory_offset - block_size
        handle.seek(block_start)
        block = handle.read(block_size)
    if len(block) != block_size:
        raise RuntimeError("APK signing block is truncated")
    first_size = struct.unpack_from("<Q", block, 0)[0]
    if first_size != block_size_without_first_size:
        raise RuntimeError("APK signing block size fields do not match")
    return block


def inject_apk_signing_block(source: Path, output: Path, signing_block: bytes) -> None:
    eocd_offset, central_directory_offset = _find_eocd(source)
    if central_directory_offset + len(signing_block) >= 0xFFFFFFFF:
        raise RuntimeError(
            "APK central directory offset exceeds ZIP32 after signing-block insertion"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle, output.open("wb") as output_handle:
        remaining = central_directory_offset
        while remaining:
            chunk = input_handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError("APK ended before its central directory")
            output_handle.write(chunk)
            remaining -= len(chunk)
        output_handle.write(signing_block)
        suffix = bytearray(input_handle.read())

    relative_eocd = eocd_offset - central_directory_offset
    if relative_eocd < 0 or relative_eocd + ZIP_EOCD_MIN_SIZE > len(suffix):
        raise RuntimeError("APK EOCD is outside the central-directory suffix")
    struct.pack_into(
        "<I",
        suffix,
        relative_eocd + 16,
        central_directory_offset + len(signing_block),
    )
    with output.open("ab") as output_handle:
        output_handle.write(suffix)

    if extract_apk_signing_block(output) != signing_block:
        raise RuntimeError("failed to preserve the original APK signing block")
    with zipfile.ZipFile(output, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("prepared LSPatch input contains a corrupt ZIP entry")


def prepare_lspatch_input(
    unsigned: Path, output: Path, sdk: Path, original_play_apk: Path
) -> None:
    _, _, zipalign, _ = android_tools(sdk)
    aligned = output.with_suffix(".aligned.apk")
    run([str(zipalign), "-f", "-p", "4", str(unsigned), str(aligned)])
    signing_block = extract_apk_signing_block(original_play_apk)
    inject_apk_signing_block(aligned, output, signing_block)
    aligned.unlink(missing_ok=True)


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
    parser.add_argument("--keystore", type=Path)
    parser.add_argument("--key-alias")
    parser.add_argument("--ks-pass-env", default="ASTRAL_ANDROID_KEYSTORE_PASSWORD")
    parser.add_argument("--key-pass-env", default="ASTRAL_ANDROID_KEY_PASSWORD")
    parser.add_argument("--apktool", default="apktool")
    parser.add_argument(
        "--prepare-for-lspatch",
        action="store_true",
        help="Build a zipaligned modified APK and transplant the original Play signing block for final LSPatch processing",
    )
    parser.add_argument("--android-sdk", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.base_apk.is_file():
        raise RuntimeError(f"base APK not found: {args.base_apk}")
    if not args.font_file.is_file():
        raise RuntimeError(f"Korean legacy font not found: {args.font_file}")
    if not args.prepare_for_lspatch:
        if args.keystore is None or not args.keystore.is_file():
            raise RuntimeError(f"Android signing keystore not found: {args.keystore}")
        if not args.key_alias:
            raise RuntimeError("--key-alias is required when producing a signed APK")
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
        version_name = read_apk_version_name(base_apk, sdk)
        compiled_manifest, game_activity, _manifest_version = compile_manifest(
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
        config = build_runtime_config(args.release_index_url, game_activity)
        injected = work / "runtime-injected.apk"
        assemble_unsigned_apk(
            base_apk,
            compiled_manifest,
            patched_unity,
            runtime_dex,
            config,
            injected,
        )
        if args.prepare_for_lspatch:
            prepare_lspatch_input(injected, output, sdk, base_apk)
        else:
            assert args.keystore is not None
            assert args.key_alias is not None
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
