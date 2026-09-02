#!/usr/bin/env python3
"""Validate untouched Google Play split APKs and prepare release metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

PACKAGE_NAME = "com.feimo.astralpartyjpn"
REPOSITORY = "maynut02/astral-party-korean-patch"
PACKAGE_ATTRIBUTE = re.compile(r"([A-Za-z][A-Za-z0-9]*)='([^']*)'")
CERTIFICATE_LINE = re.compile(
    r"^Signer #[0-9]+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ApkMetadata:
    source: Path
    package_name: str
    version_name: str
    version_code: int
    split_name: str | None
    certificate_sha256: str
    sha256: str
    size: int


def _run(*command: str) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout + result.stderr


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect(apk: Path, aapt: str, apksigner: str) -> ApkMetadata:
    badging = _run(aapt, "dump", "badging", str(apk))
    package_line = next(
        (line for line in badging.splitlines() if line.startswith("package: ")),
        None,
    )
    if package_line is None:
        raise ValueError(f"aapt did not report package metadata for {apk}")
    attributes = dict(PACKAGE_ATTRIBUTE.findall(package_line))
    package_name = attributes.get("name", "")
    version_name = attributes.get("versionName", "")
    raw_version_code = attributes.get("versionCode", "")
    if not raw_version_code.isdigit():
        raise ValueError(f"invalid versionCode in {apk}: {raw_version_code!r}")

    signer_output = _run(
        apksigner,
        "verify",
        "--verbose",
        "--print-certs",
        str(apk),
    )
    certificates = {
        match.lower() for match in CERTIFICATE_LINE.findall(signer_output)
    }
    if len(certificates) != 1:
        raise ValueError(f"expected one APK signing certificate in {apk}")

    return ApkMetadata(
        source=apk,
        package_name=package_name,
        version_name=version_name,
        version_code=int(raw_version_code),
        split_name=attributes.get("split") or None,
        certificate_sha256=next(iter(certificates)),
        sha256=_sha256(apk),
        size=apk.stat().st_size,
    )


def _release_name(index: int, split_name: str | None) -> str:
    if split_name is None:
        return "base.apk"
    safe_split = re.sub(r"[^A-Za-z0-9._-]", "_", split_name).strip("._-")
    if not safe_split:
        safe_split = "split"
    return f"split-{index:03d}-{safe_split[:80]}.apk"


def prepare(
    input_dir: Path,
    output_dir: Path,
    aapt: str,
    apksigner: str,
    device_profile: str,
) -> dict[str, object]:
    apk_paths = sorted(input_dir.rglob("*.apk"))
    if not apk_paths:
        raise ValueError("Google Play download did not contain any APK files")

    inspected = [_inspect(path, aapt, apksigner) for path in apk_paths]
    bases = [item for item in inspected if item.split_name is None]
    if len(bases) != 1:
        raise ValueError(f"expected exactly one base APK, found {len(bases)}")

    expected = bases[0]
    for item in inspected:
        if item.package_name != PACKAGE_NAME:
            raise ValueError(f"unexpected package in {item.source}: {item.package_name}")
        if item.version_code != expected.version_code:
            raise ValueError(f"mixed versionCode in {item.source}")
        if item.certificate_sha256 != expected.certificate_sha256:
            raise ValueError(f"mixed signing certificate in {item.source}")

    split_names = [item.split_name for item in inspected if item.split_name is not None]
    if len(split_names) != len(set(split_names)):
        raise ValueError("download contains duplicate split names")

    ordered = [expected] + sorted(
        (item for item in inspected if item.split_name is not None),
        key=lambda item: item.split_name or "",
    )
    tag = f"android-game-v{expected.version_code}"
    asset_base = f"https://github.com/{REPOSITORY}/releases/download/{tag}"
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []
    for index, item in enumerate(ordered):
        name = _release_name(index, item.split_name)
        target = output_dir / name
        shutil.copyfile(item.source, target)
        if _sha256(target) != item.sha256:
            raise ValueError(f"copied APK hash changed: {name}")
        files.append(
            {
                "name": name,
                "splitName": item.split_name,
                "downloadUrl": f"{asset_base}/{name}",
                "sha256": item.sha256,
                "size": item.size,
            }
        )

    payload: dict[str, object] = {
        "schemaVersion": 1,
        "packageName": expected.package_name,
        "versionName": expected.version_name,
        "versionCode": expected.version_code,
        "certificateSha256": expected.certificate_sha256,
        "deviceProfile": device_profile,
        "releaseTag": tag,
        "files": files,
    }
    metadata = output_dir / "AstralPartyOriginal.json"
    metadata.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--aapt", required=True)
    parser.add_argument("--apksigner", required=True)
    parser.add_argument("--device-profile", required=True)
    args = parser.parse_args()
    payload = prepare(
        args.input_dir,
        args.output_dir,
        args.aapt,
        args.apksigner,
        args.device_profile,
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
