import gzip
from pathlib import Path

from astral_builder.release.transport import gzip_payload


def test_gzip_payload_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes((b"astral-patch-data\n" * 4096) + b"tail")

    first = gzip_payload(source, tmp_path / "first.gz")
    second = gzip_payload(source, tmp_path / "second.gz")

    assert first.sha256 == second.sha256
    assert first.size == second.size
    assert first.path.read_bytes() == second.path.read_bytes()
    assert gzip.decompress(first.path.read_bytes()) == source.read_bytes()
    assert first.size < source.stat().st_size
