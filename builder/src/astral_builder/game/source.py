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

    @property
    def catalog_hash_url(self) -> str:
        return _source_file_url(self.source_url, f"catalog_{self.version}.hash")


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


def _revision_from_source_url(source_url: str, route: str, version: str) -> str:
    # Keep this grammar in sync with the standalone tools/patch_watcher.py parser.
    error = f"invalid route/version/revision in sourceUrl for {route}/{version}: {source_url}"
    try:
        parsed = urllib.parse.urlsplit(source_url)
    except ValueError as exc:
        raise SourceDiscoveryError(error) from exc
    path_parts = parsed.path.removeprefix("/").removesuffix("/").split("/")
    if parsed.scheme not in ("http", "https") or not parsed.netloc or any(
        part in ("", ".", "..") for part in path_parts
    ):
        raise SourceDiscoveryError(error)

    if route in path_parts:
        # A CDN prefix is allowed, but the route/version/revision tail is fixed.
        tail = path_parts[path_parts.index(route) :]
        if len(tail) not in (3, 4) or tail[1] != version:
            raise SourceDiscoveryError(error)
        revision = tail[2]
    elif len(path_parts) == 1 or (len(path_parts) == 2 and path_parts[0] == "game"):
        # Compatibility for short, unstructured numeric source URLs only.
        revision = path_parts[-1]
    else:
        raise SourceDiscoveryError(error)
    if not revision.isascii() or not revision.isdecimal():
        raise SourceDiscoveryError(error)
    return revision


def _source_file_url(source_url: str, filename: str) -> str:
    parsed = urllib.parse.urlsplit(source_url)
    return parsed._replace(path=f"{parsed.path.rstrip('/')}/{filename}").geturl()


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
        revision = _revision_from_source_url(source_url, route, version)
        parsed = urllib.parse.urlsplit(source_url)
        source_url = parsed._replace(path=parsed.path.rstrip("/")).geturl()
        return GameSource(
            route=route,
            version=version,
            revision=revision,
            source_url=source_url,
            catalog_url=_source_file_url(source_url, f"catalog_{version}.json"),
        )

    def fetch_catalog_hash(self, source: GameSource) -> str:
        payload = self._fetch(source.catalog_hash_url, self._timeout)
        try:
            value = payload.decode("ascii").strip().lower()
        except UnicodeDecodeError as exc:
            raise SourceDiscoveryError("catalog hash is not ASCII") from exc
        if len(value) != 32 or any(char not in "0123456789abcdef" for char in value):
            raise SourceDiscoveryError("catalog hash is not 32-character lowercase hex")
        return value

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
