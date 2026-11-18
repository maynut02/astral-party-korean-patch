from __future__ import annotations

import hashlib
import http.client
import os
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from astral_builder.addressables.resolver import BundleOrigin, ResolvedBundle


class BundleDownloadError(RuntimeError):
    """Raised when a resolved remote bundle cannot be downloaded or validated."""


@dataclass(frozen=True, slots=True)
class DownloadedBundle:
    resolved: ResolvedBundle
    path: Path
    sha256: str
    size: int


FetchBytes = Callable[[str, float], bytes]

FETCH_ATTEMPTS = 4
FETCH_RETRY_BASE_DELAY = 2.0


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return True


def _default_fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "astral-party-korean-builder/0.1"},
    )
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (OSError, http.client.HTTPException) as exc:
            # CDN resets surface from getresponse()/read() unwrapped, not as URLError.
            if attempt == FETCH_ATTEMPTS or not _is_retryable(exc):
                raise BundleDownloadError(
                    f"bundle request failed after {attempt} attempt(s): {url}: {exc!r}"
                ) from exc
            time.sleep(FETCH_RETRY_BASE_DELAY * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


class RemoteBundleDownloader:
    def __init__(self, *, fetch: FetchBytes = _default_fetch, timeout: float = 120.0) -> None:
        self._fetch = fetch
        self._timeout = timeout

    def download(self, bundle: ResolvedBundle, destination: str | Path) -> DownloadedBundle:
        if bundle.origin is not BundleOrigin.REMOTE or bundle.download_url is None:
            raise BundleDownloadError(f"bundle is not remotely downloadable: {bundle.primary_key}")

        payload = self._fetch(bundle.download_url, self._timeout)
        size = len(payload)
        if bundle.expected_size > 0 and size != bundle.expected_size:
            raise BundleDownloadError(
                f"bundle size mismatch for {bundle.primary_key}: "
                f"expected {bundle.expected_size}, got {size}"
            )

        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(payload).hexdigest()
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, destination)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

        return DownloadedBundle(resolved=bundle, path=destination, sha256=digest, size=size)

    def download_target(
        self,
        bundles: tuple[ResolvedBundle, ...],
        destination_root: str | Path,
    ) -> tuple[DownloadedBundle, ...]:
        root = Path(destination_root)
        results: list[DownloadedBundle] = []
        seen: set[tuple[str, str]] = set()
        for bundle in bundles:
            if bundle.origin is not BundleOrigin.REMOTE:
                continue
            identity = (bundle.bundle_name, bundle.cache_hash)
            if identity in seen:
                continue
            seen.add(identity)
            destination = root / bundle.cache_hash / f"{bundle.cache_hash}.bundle"
            results.append(self.download(bundle, destination))
        return tuple(results)
