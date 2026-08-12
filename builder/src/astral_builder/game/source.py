from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class SourceDiscoveryError(RuntimeError):
    """Raised when the game content endpoint returns invalid or unusable metadata."""


@dataclass(frozen=True, slots=True)
class GameSource:
    route: str
    version: str
    revision: str
    source_url: str
    catalog_url: str


@dataclass(frozen=True, slots=True)
class DownloadedCatalog:
    path: Path
    sha256: str
    size: int


FetchBytes = Callable[[str, float], bytes]


def _default_fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "astral-party-korean-builder/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise SourceDiscoveryError(f"request failed: {url}: {exc}") from exc


def _host_for_route(route: str) -> str:
    route = route.upper()
    if route.startswith("INT_"):
        return "selist.feimogames.com"
    if route.startswith("CN_"):
        return "se-web-cn.feimogames.com"
    raise SourceDiscoveryError(f"unsupported route: {route}")


def _revision_from_source_url(source_url: str) -> str:
    path_parts = [part for part in urllib.parse.urlparse(source_url).path.split("/") if part]
    if not path_parts:
        raise SourceDiscoveryError(f"sourceUrl has no revision path segment: {source_url}")
    revision = path_parts[-1]
    if not revision or any(char in revision for char in '<>:"/\\|?*\x00'):
        raise SourceDiscoveryError(f"invalid revision in sourceUrl: {source_url}")
    return revision


class GameSourceClient:
    def __init__(self, *, fetch: FetchBytes = _default_fetch, timeout: float = 30.0) -> None:
        self._fetch = fetch
        self._timeout = timeout

    def hotaddress_url(self, route: str, version: str) -> str:
        if not version.strip():
            raise SourceDiscoveryError("game version cannot be empty")
        host = _host_for_route(route)
        query = urllib.parse.urlencode({"route": route.upper(), "version": version})
        return f"http://{host}:7878/api/hotaddressExtend/get?{query}"

    def discover(self, route: str, version: str) -> GameSource:
        route = route.upper()
        raw = self._fetch(self.hotaddress_url(route, version), self._timeout)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceDiscoveryError("hotaddress endpoint returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SourceDiscoveryError("hotaddress response must be a JSON object")
        source_url = payload.get("sourceUrl")
        if not isinstance(source_url, str) or not source_url.strip():
            raise SourceDiscoveryError("hotaddress response has no sourceUrl")
        source_url = source_url.rstrip("/")
        revision = _revision_from_source_url(source_url)
        return GameSource(
            route=route,
            version=version,
            revision=revision,
            source_url=source_url,
            catalog_url=f"{source_url}/catalog_{version}.json",
        )

    def download_catalog(self, source: GameSource, destination: str | Path) -> DownloadedCatalog:
        payload = self._fetch(source.catalog_url, self._timeout)
        # Fail before touching the destination if the server did not return a JSON catalog.
        try:
            decoded = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceDiscoveryError("catalog endpoint returned invalid JSON") from exc
        if not isinstance(decoded, dict) or "m_InternalIds" not in decoded:
            raise SourceDiscoveryError("downloaded JSON is not an Addressables catalog")

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

        return DownloadedCatalog(path=destination, sha256=digest, size=len(payload))
