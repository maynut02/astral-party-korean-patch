import io
import urllib.error
from pathlib import Path

import pytest

from astral_builder.addressables.resolver import BundleOrigin, ResolvedBundle
from astral_builder.source import BundleDownloadError, RemoteBundleDownloader
from astral_builder.source import downloader as downloader_module


def _bundle(*, size: int = 4, origin: BundleOrigin = BundleOrigin.REMOTE) -> ResolvedBundle:
    return ResolvedBundle(
        origin=origin,
        primary_key="target.bundle",
        internal_id="{App.WebServerConfig.Path}/target.bundle",
        bundle_name="bundle-root",
        cache_hash="a" * 32,
        expected_size=size,
        download_url="https://cdn.example/target.bundle" if origin is BundleOrigin.REMOTE else None,
        cache_relative_path=f"bundle-root/{'a' * 32}/__data",
    )


def test_download_validates_size_and_writes_atomically(tmp_path: Path) -> None:
    downloader = RemoteBundleDownloader(fetch=lambda _url, _timeout: b"data")
    output = tmp_path / "target.bundle"
    result = downloader.download(_bundle(), output)
    assert result.path == output
    assert output.read_bytes() == b"data"
    assert result.size == 4
    assert len(result.sha256) == 64


def test_download_rejects_size_mismatch_without_writing(tmp_path: Path) -> None:
    downloader = RemoteBundleDownloader(fetch=lambda _url, _timeout: b"bad")
    output = tmp_path / "target.bundle"
    with pytest.raises(BundleDownloadError, match="size mismatch"):
        downloader.download(_bundle(size=4), output)
    assert not output.exists()


def test_download_target_skips_runtime_bundles(tmp_path: Path) -> None:
    calls = []

    def fetch(url: str, _timeout: float) -> bytes:
        calls.append(url)
        return b"data"

    downloader = RemoteBundleDownloader(fetch=fetch)
    results = downloader.download_target(
        (_bundle(), _bundle(origin=BundleOrigin.RUNTIME)),
        tmp_path,
    )
    assert len(results) == 1
    assert calls == ["https://cdn.example/target.bundle"]


def test_default_fetch_retries_connection_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes: list[BaseException | bytes] = [ConnectionResetError(104, "reset"), b"data"]
    sleeps: list[float] = []

    def fake_urlopen(_request, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return io.BytesIO(outcome)

    monkeypatch.setattr(downloader_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(downloader_module.time, "sleep", sleeps.append)
    result = RemoteBundleDownloader().download(_bundle(), tmp_path / "target.bundle")
    assert result.path.read_bytes() == b"data"
    assert outcomes == []
    assert len(sleeps) == 1


def test_default_fetch_does_not_retry_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(downloader_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(downloader_module.time, "sleep", lambda _delay: None)
    with pytest.raises(BundleDownloadError, match="after 1 attempt"):
        downloader_module._default_fetch("https://cdn.example/target.bundle", 1.0)
    assert calls == ["https://cdn.example/target.bundle"]
