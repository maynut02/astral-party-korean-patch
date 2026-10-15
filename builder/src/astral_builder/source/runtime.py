from __future__ import annotations

import hashlib
from pathlib import Path

import UnityPy

from astral_builder.addressables.resolver import BundleOrigin, ResolvedBundle
from astral_builder.source.downloader import BundleDownloadError, DownloadedBundle

_RUNTIME_PREFIX = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/"


class RuntimeBundleError(BundleDownloadError):
    """Raised when a catalog runtime bundle cannot be read or verified safely."""


def runtime_bundle_relative_path(bundle: ResolvedBundle) -> str:
    """Return a safe, slash-separated path relative to StreamingAssets/aa.

    Validate the original internal ID rather than a resolver cache path: stripping
    leading separators or resolving dot segments would conceal unsafe catalog IDs.
    """
    if bundle.origin is not BundleOrigin.RUNTIME:
        raise RuntimeBundleError(f"bundle is not a runtime bundle: {bundle.primary_key}")
    internal_id = bundle.internal_id.replace("\\", "/")
    if not internal_id.startswith(_RUNTIME_PREFIX):
        raise RuntimeBundleError(f"invalid runtime bundle internal id: {bundle.internal_id!r}")
    relative = internal_id[len(_RUNTIME_PREFIX) :]
    parts = relative.split("/")
    if (
        any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in relative)
        or any(char in relative for char in ':?#%*"<>|')
        or any(not part or part in (".", "..") or part.endswith((".", " ")) for part in parts)
    ):
        raise RuntimeBundleError(f"unsafe runtime bundle path: {bundle.internal_id!r}")
    return "/".join(parts)


def load_runtime_bundle(bundle: ResolvedBundle, game_data_dir: Path) -> DownloadedBundle:
    """Read a runtime bundle inside the game data directory and verify its identity.

    As with remote downloads, a zero catalog size means that no size is specified.
    The SHA-256 and size describe the same bytes inspected by UnityPy.
    """
    relative = runtime_bundle_relative_path(bundle)
    try:
        root = Path(game_data_dir).resolve(strict=True)
        aa_root = (root / "StreamingAssets" / "aa").resolve(strict=True)
        if not aa_root.is_relative_to(root):
            raise RuntimeBundleError("runtime StreamingAssets/aa path escapes game data directory")
        path = (aa_root / relative).resolve(strict=True)
        if not path.is_relative_to(aa_root):
            raise RuntimeBundleError(f"runtime bundle path escapes StreamingAssets/aa: {relative}")
        payload = path.read_bytes()
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, RuntimeBundleError):
            raise
        raise RuntimeBundleError(f"cannot read runtime bundle {relative!r}: {exc}") from exc

    size = len(payload)
    if bundle.expected_size < 0 or (bundle.expected_size > 0 and size != bundle.expected_size):
        raise RuntimeBundleError(
            f"runtime bundle size mismatch for {bundle.primary_key}: "
            f"expected {bundle.expected_size}, got {size}"
        )

    try:
        environment = UnityPy.load(payload)
        names = [
            str(getattr(obj.read(), "m_Name", "") or "")
            for obj in environment.objects
            if getattr(getattr(obj, "type", None), "name", "") == "AssetBundle"
        ]
    except Exception as exc:
        raise RuntimeBundleError(f"cannot parse runtime AssetBundle {relative!r}: {exc}") from exc
    if len(names) != 1:
        raise RuntimeBundleError(
            f"expected one AssetBundle object in {relative!r}, found {len(names)}"
        )
    actual = names[0]
    if (
        not bundle.bundle_name
        or not actual
        or (actual.removesuffix(".bundle") != bundle.bundle_name.removesuffix(".bundle"))
    ):
        raise RuntimeBundleError(
            f"runtime AssetBundle name mismatch for {relative!r}: "
            f"expected={bundle.bundle_name!r} actual={actual!r}"
        )
    return DownloadedBundle(bundle, path, hashlib.sha256(payload).hexdigest(), size)
