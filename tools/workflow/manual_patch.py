from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class SteamRouteLayout:
    locallow_dir: str
    executable_dir: str
    data_dir: str


ROUTE_LAYOUTS = {
    "INT_STEAM": SteamRouteLayout(
        locallow_dir="AstralParty_INT",
        executable_dir="8vJXnINT",
        data_dir="AstralParty_INT_Data",
    ),
    "CN_STEAM": SteamRouteLayout(
        locallow_dir="AstralParty_CN",
        executable_dir="8vJXn6CN",
        data_dir="AstralParty_CN_Data",
    ),
}

ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
README_NAME = "설치방법.txt"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe manifest path: {value!r}")
    return path


def _payload_name(download_url: str) -> str:
    name = PurePosixPath(urllib.parse.urlparse(download_url).path).name
    if not name.endswith(".gz"):
        raise ValueError(f"manual package source is not a gzip asset: {download_url}")
    return name[:-3]


def _archive_path(layout: SteamRouteLayout, target: str, relative_path: str) -> PurePosixPath:
    relative = _safe_relative_path(relative_path)
    if target == "addressables":
        return (
            PurePosixPath(layout.locallow_dir)
            / "com.unity.addressables"
            / "AssetBundles"
            / relative
        )
    if target == "game-data" and relative == PurePosixPath("data.unity3d"):
        return PurePosixPath(layout.executable_dir) / layout.data_dir / relative
    raise ValueError(f"unsupported manual patch target/path: {target}/{relative}")


def _readme(route: str, manifest: dict[str, object]) -> str:
    layout = ROUTE_LAYOUTS[route]
    game = manifest.get("game")
    patch = manifest.get("patch")
    if not isinstance(game, dict) or not isinstance(patch, dict):
        raise TypeError("manifest is missing game/patch metadata")
    game_version = str(game.get("version", "unknown"))
    revision = str(game.get("revision", "unknown"))
    patch_version = str(patch.get("version", "unknown"))

    return f"""Astral Party 한국어 패치 - {route} 수동 설치

패치 버전: {patch_version}
게임 버전: {game_version}
게임 리비전: {revision}

[설치 전]
- 게임을 완전히 종료하세요.
- 수동 설치는 원본 파일 자동 백업/복구를 제공하지 않습니다. 필요한 경우 원본 파일을 직접 백업하세요.

[설치 방법]
1. {layout.locallow_dir} 폴더를 아래 경로에 복사하고 기존 파일을 덮어씁니다.
   %USERPROFILE%\\AppData\\LocalLow\\feimo\\

2. {layout.executable_dir} 폴더를 Astral Party 설치 폴더에 복사하고 기존 파일을 덮어씁니다.
   예: ...\\Steam\\steamapps\\common\\Astral Party\\

3. 게임을 실행합니다.

[참고]
- 이 압축파일은 수동 설치용입니다.
- 자동 설치/업데이트 및 원본 복구가 필요하면 AstralAutoPatcher 사용을 권장합니다.
"""


def build_manual_patch(manifest_path: Path, payloads_dir: Path, output_path: Path) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 2:
        raise ValueError("unsupported patch manifest schema")

    patch = manifest.get("patch")
    files = manifest.get("files")
    if not isinstance(patch, dict) or not isinstance(files, list) or not files:
        raise ValueError("manifest is missing patch/files data")

    route = patch.get("route")
    if not isinstance(route, str) or route not in ROUTE_LAYOUTS:
        raise ValueError(f"manual patch is only supported for Steam routes: {route!r}")
    layout = ROUTE_LAYOUTS[route]

    entries: list[tuple[Path, PurePosixPath]] = []
    archive_paths: set[PurePosixPath] = set()
    for item in files:
        if not isinstance(item, dict):
            raise TypeError("manifest file entry is not an object")
        target = item.get("target")
        relative_path = item.get("path")
        download_url = item.get("downloadUrl")
        expected_sha256 = item.get("sha256")
        expected_size = item.get("size")
        if not isinstance(target, str) or not isinstance(relative_path, str):
            raise TypeError("manifest file target/path is invalid")
        if not isinstance(download_url, str) or not isinstance(expected_sha256, str):
            raise TypeError("manifest file downloadUrl/sha256 is invalid")
        if not isinstance(expected_size, int) or expected_size <= 0:
            raise ValueError("manifest file size is invalid")

        payload = payloads_dir / _payload_name(download_url)
        if not payload.is_file():
            raise FileNotFoundError(f"manual patch payload is missing: {payload}")
        actual_size = payload.stat().st_size
        if actual_size != expected_size:
            raise ValueError(
                f"manual patch payload size mismatch: {payload.name}: "
                f"expected {expected_size}, actual {actual_size}"
            )
        actual_sha256 = _sha256(payload)
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"manual patch payload SHA-256 mismatch: {payload.name}: "
                f"expected {expected_sha256}, actual {actual_sha256}"
            )

        archive_path = _archive_path(layout, target, relative_path)
        if archive_path in archive_paths:
            raise ValueError(f"duplicate manual patch archive path: {archive_path}")
        archive_paths.add(archive_path)
        entries.append((payload, archive_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        readme = zipfile.ZipInfo(README_NAME, ZIP_TIMESTAMP)
        readme.compress_type = zipfile.ZIP_DEFLATED
        readme.external_attr = 0o100644 << 16
        archive.writestr(readme, _readme(route, manifest).encode("utf-8"))

        for payload, archive_path in sorted(entries, key=lambda item: item[1].as_posix()):
            info = zipfile.ZipInfo(archive_path.as_posix(), ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with payload.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Steam manual-install patch ZIP.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--payloads-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = build_manual_patch(args.manifest, args.payloads_dir, args.output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
