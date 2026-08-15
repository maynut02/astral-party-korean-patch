from __future__ import annotations

import argparse
import re
from collections.abc import Iterable
from pathlib import Path


def next_immutable_tag(base: str, existing_tags: Iterable[str]) -> str:
    highest = 0
    pattern = re.compile(rf"^{re.escape(base)}_p([0-9]+)$")
    for raw in existing_tags:
        tag = raw.strip()
        if tag == base:
            highest = max(highest, 1)
            continue
        match = pattern.fullmatch(tag)
        if match:
            highest = max(highest, int(match.group(1)))
    if highest == 0:
        return base
    return f"{base}_p{highest + 1}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Choose the next immutable patch release tag.")
    parser.add_argument("--base", required=True)
    parser.add_argument("--tags-file", required=True, type=Path)
    args = parser.parse_args(argv)
    tags = args.tags_file.read_text(encoding="utf-8").splitlines()
    print(next_immutable_tag(args.base, tags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
