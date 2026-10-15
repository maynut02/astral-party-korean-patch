from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import UnityPy

from astral_builder.addressables.resolver import BundleOrigin, ResolvedBundle
from astral_builder.source import runtime
from astral_builder.source.runtime import (
    RuntimeBundleError,
    load_runtime_bundle,
    runtime_bundle_relative_path,
)

PREFIX = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/"
RELATIVE = "StandaloneWindows64/current-font.bundle"
PAYLOAD = b"fixture bundle bytes"


def _bundle(relative: str = RELATIVE, **changes) -> ResolvedBundle:
    bundle = ResolvedBundle(
        origin=BundleOrigin.RUNTIME,
        primary_key="current-font.bundle",
        internal_id=f"{PREFIX}{relative}",
        bundle_name="original-bundle-root",
        cache_hash="a" * 32,
        expected_size=len(PAYLOAD),
        download_url=None,
        cache_relative_path=f"original-bundle-root/{'a' * 32}/__data",
    )
    return replace(bundle, **changes)


def _environment(*names: str) -> SimpleNamespace:
    objects = [
        SimpleNamespace(type=SimpleNamespace(name="Texture2D")),
        *(
            SimpleNamespace(
                type=SimpleNamespace(name="AssetBundle"),
                read=lambda name=name: SimpleNamespace(m_Name=name),
            )
            for name in names
        ),
    ]
    return SimpleNamespace(objects=objects)


def _write_bundle(root: Path, relative: str = RELATIVE) -> Path:
    path = root / "StreamingAssets" / "aa" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    return path


def _link_directory(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        # Windows junctions exercise resolve() containment without requiring
        # Developer Mode or the privilege needed for directory symlinks.
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:ASTRAL_TEST_LINK "
                "-Target $env:ASTRAL_TEST_TARGET -ErrorAction Stop | Out-Null",
            ],
            env={**os.environ, "ASTRAL_TEST_LINK": str(link), "ASTRAL_TEST_TARGET": str(target)},
            check=True,
            capture_output=True,
        )


@pytest.mark.parametrize("relative", [RELATIVE, RELATIVE.replace("/", "\\")])
def test_runtime_relative_path_normalizes_separators(relative: str) -> None:
    assert runtime_bundle_relative_path(_bundle(relative)) == RELATIVE


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "/absolute.bundle",
        "//server/share.bundle",
        "\\absolute.bundle",
        "\\\\server\\share.bundle",
        "C:/font.bundle",
        "C:font.bundle",
        "StandaloneWindows64/D:/font.bundle",
        "../font.bundle",
        "x/../../font.bundle",
        "x/../font.bundle",
        "x\\..\\font.bundle",
        "./font.bundle",
        "x/./font.bundle",
        "x//font.bundle",
        "x/",
        "x/font.bundle?token=value",
        "x/font.bundle#fragment",
        "x/font.bundle:stream",
        "x/\x00font.bundle",
        "x/\nfont.bundle",
        "x/\rfont.bundle",
        "x/\tfont.bundle",
        "x/\x7ffont.bundle",
        "x/\x85font.bundle",
        "x/%2e%2e/font.bundle",
        "x/%2Ffont.bundle",
        "x/font.bundle.",
        "x/font.bundle ",
        "x/*.bundle",
    ],
)
def test_rejects_unsafe_catalog_runtime_paths(relative: str) -> None:
    with pytest.raises(RuntimeBundleError, match="unsafe runtime bundle path"):
        runtime_bundle_relative_path(_bundle(relative))


@pytest.mark.parametrize(
    "internal_id",
    ["/absolute.bundle", "C:\\font.bundle", "https://cdn.example/font.bundle", "font.bundle"],
)
def test_requires_original_runtime_placeholder(internal_id: str) -> None:
    with pytest.raises(RuntimeBundleError, match="internal id"):
        runtime_bundle_relative_path(_bundle(internal_id=internal_id))


def test_rejects_remote_bundle_even_with_runtime_internal_id(tmp_path: Path) -> None:
    bundle = _bundle(origin=BundleOrigin.REMOTE)
    with pytest.raises(RuntimeBundleError, match="not a runtime bundle"):
        load_runtime_bundle(bundle, tmp_path)


@pytest.mark.parametrize("catalog_suffix", ["", ".bundle"])
@pytest.mark.parametrize("actual_suffix", ["", ".bundle"])
def test_reads_runtime_bytes_and_matches_original_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog_suffix: str, actual_suffix: str
) -> None:
    root = tmp_path / "Game_Data"
    path = _write_bundle(root)
    inspected = []

    def load(payload: bytes):
        inspected.append(payload)
        return _environment(f"original-bundle-root{actual_suffix}")

    monkeypatch.setattr(runtime.UnityPy, "load", load)
    bundle = _bundle(bundle_name=f"original-bundle-root{catalog_suffix}")
    result = load_runtime_bundle(bundle, root)

    assert result.resolved is bundle
    assert result.path == path.resolve()
    assert result.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert result.size == len(PAYLOAD)
    assert inspected == [PAYLOAD]
    assert path.read_bytes() == PAYLOAD


def test_missing_runtime_bundle_is_actionable(tmp_path: Path) -> None:
    (tmp_path / "StreamingAssets" / "aa").mkdir(parents=True)
    with pytest.raises(RuntimeBundleError, match="cannot read runtime bundle"):
        load_runtime_bundle(_bundle(), tmp_path)


@pytest.mark.parametrize("expected_size", [len(PAYLOAD) - 1, len(PAYLOAD) + 1, -1])
def test_checks_catalog_size_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expected_size: int
) -> None:
    _write_bundle(tmp_path)

    def unexpected_parse(_payload):
        pytest.fail("size mismatch must be rejected before Unity parsing")

    monkeypatch.setattr(runtime.UnityPy, "load", unexpected_parse)
    with pytest.raises(RuntimeBundleError, match="size mismatch"):
        load_runtime_bundle(_bundle(expected_size=expected_size), tmp_path)


def test_zero_catalog_size_is_unspecified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_bundle(tmp_path)
    monkeypatch.setattr(runtime.UnityPy, "load", lambda _: _environment("original-bundle-root"))
    assert load_runtime_bundle(_bundle(expected_size=0), tmp_path).size == len(PAYLOAD)


@pytest.mark.parametrize("actual", ["wrong-root.bundle", "current-font.bundle", "", "ORIGINAL"])
def test_rejects_assetbundle_name_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actual: str
) -> None:
    _write_bundle(tmp_path)
    monkeypatch.setattr(runtime.UnityPy, "load", lambda _: _environment(actual))
    with pytest.raises(RuntimeBundleError, match="name mismatch"):
        load_runtime_bundle(_bundle(), tmp_path)


@pytest.mark.parametrize("names", [(), ("original-bundle-root", "original-bundle-root")])
def test_requires_exactly_one_assetbundle_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]
) -> None:
    _write_bundle(tmp_path)
    monkeypatch.setattr(runtime.UnityPy, "load", lambda _: _environment(*names))
    with pytest.raises(RuntimeBundleError, match="expected one AssetBundle object"):
        load_runtime_bundle(_bundle(), tmp_path)


def test_rejects_invalid_unity_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_bundle(tmp_path)

    def corrupt_bundle(_payload):
        raise ValueError("invalid UnityFS header")

    monkeypatch.setattr(runtime.UnityPy, "load", corrupt_bundle)
    with pytest.raises(RuntimeBundleError, match="cannot parse runtime AssetBundle"):
        load_runtime_bundle(_bundle(), tmp_path)


@pytest.mark.parametrize("link_level", ["StreamingAssets", "aa", "bundle"])
def test_rejects_symlink_or_junction_escape(tmp_path: Path, link_level: str) -> None:
    root = tmp_path / "Game_Data"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    if link_level == "StreamingAssets":
        _write_bundle(outside)
        _link_directory(root / "StreamingAssets", outside / "StreamingAssets")
    elif link_level == "aa":
        _write_bundle(outside)
        (root / "StreamingAssets").mkdir()
        _link_directory(root / "StreamingAssets" / "aa", outside / "StreamingAssets" / "aa")
    else:
        (outside / "current-font.bundle").write_bytes(PAYLOAD)
        aa = root / "StreamingAssets" / "aa"
        aa.mkdir(parents=True)
        _link_directory(aa / "StandaloneWindows64", outside)
    with pytest.raises(RuntimeBundleError, match="escapes"):
        load_runtime_bundle(_bundle(), root)


def test_allows_symlink_contained_in_aa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_bundle(tmp_path)
    _link_directory(path.parent.parent / "alias", path.parent)
    monkeypatch.setattr(runtime.UnityPy, "load", lambda _: _environment("original-bundle-root"))
    result = load_runtime_bundle(_bundle("alias/current-font.bundle"), tmp_path)
    assert result.path == path.resolve()


REAL_GAME_DATA = Path(
    "C:/Program Files (x86)/Steam/steamapps/common/Astral Party/8vJXnINT/AstralParty_INT_Data"
)
REAL_RELATIVE = "StandaloneWindows64/bf9fdf4f4afafccd49732369c13d9c82.bundle"
REAL_BUNDLE = REAL_GAME_DATA / "StreamingAssets" / "aa" / REAL_RELATIVE


@pytest.mark.skipif(not REAL_BUNDLE.is_file(), reason="local Steam fixture is not installed")
def test_reads_installed_steam_tmp_bundle_without_modifying_it() -> None:
    payload = REAL_BUNDLE.read_bytes()
    result = load_runtime_bundle(
        _bundle(
            REAL_RELATIVE,
            bundle_name="bbf73ff295228ba6d340aa007997974e",
            expected_size=len(payload),
        ),
        REAL_GAME_DATA,
    )
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.size == len(payload)
    environment = UnityPy.load(payload)
    names = {
        (obj.type.name, getattr(obj.read(), "m_Name", ""))
        for obj in environment.objects
        if obj.type.name in ("MonoBehaviour", "Texture2D")
    }
    assert ("MonoBehaviour", "Afacad-Regular_TMP") in names
    assert ("Texture2D", "Afacad-Regular_TMP Atlas") in names
    assert REAL_BUNDLE.read_bytes() == payload
