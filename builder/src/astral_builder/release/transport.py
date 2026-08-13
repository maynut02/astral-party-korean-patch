from __future__ import annotations

import gzip
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TransportAsset:
    path: Path
    sha256: str
    size: int


def gzip_payload(source: str | Path, destination: str | Path) -> TransportAsset:
    """Create a deterministic gzip transport file without changing the Unity payload itself."""
    source_path = Path(source)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        with source_path.open("rb") as input_file, temp.open("wb") as raw_output:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw_output,
                compresslevel=9,
                mtime=0,
            ) as compressed:
                shutil.copyfileobj(input_file, compressed, length=1024 * 1024)
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)

    hasher = hashlib.sha256()
    size = 0
    with output.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
            size += len(chunk)
    return TransportAsset(path=output, sha256=hasher.hexdigest(), size=size)
