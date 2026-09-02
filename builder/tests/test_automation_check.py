from io import StringIO
from uuid import UUID

import pytest

from astral_builder.automation.check import RevisionCheck, check_revision, write_github_output
from astral_builder.database.repository import RevisionConflictError
from astral_builder.game.source import GameSource, GameSourceClient

REVISION_ID = UUID("00000000-0000-0000-0000-000000000116")
CATALOG_HASH = "fd58ba01bbca5e5e389b5b73240df134"


class _Cursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, _query, _params):
        return None

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.row = row

    def cursor(self):
        return _Cursor(self.row)


def _client() -> GameSourceClient:
    responses = iter(
        [
            b'{"sourceUrl":"https://cdn.example/INT_STEAM/3.2.0/116"}',
            CATALOG_HASH.encode(),
        ]
    )
    return GameSourceClient(fetch=lambda _url, _timeout: next(responses))


def test_new_revision_requires_sync_and_release() -> None:
    result = check_revision(
        _Connection(None),  # type: ignore[arg-type]
        route="INT_STEAM",
        game_version="3.2.0",
        client=_client(),
    )
    assert result.changed is True
    assert result.sync_required is True
    assert result.revision_id is None
    assert result.source.revision == "116"
    assert result.catalog_hash == CATALOG_HASH


def test_processed_revision_with_release_is_unchanged() -> None:
    result = check_revision(
        _Connection((REVISION_ID, CATALOG_HASH, object(), True)),  # type: ignore[arg-type]
        route="INT_STEAM",
        game_version="3.2.0",
        client=_client(),
    )
    assert result.changed is False
    assert result.sync_required is False
    assert result.revision_id == str(REVISION_ID)



def test_processed_revision_without_published_build_retries_without_resync() -> None:
    result = check_revision(
        _Connection((REVISION_ID, CATALOG_HASH, object(), False)),  # type: ignore[arg-type]
        route="INT_STEAM",
        game_version="3.2.0",
        client=_client(),
    )
    assert result.changed is True
    assert result.sync_required is False


def test_unprocessed_revision_requires_sync() -> None:
    result = check_revision(
        _Connection((REVISION_ID, CATALOG_HASH, None, False)),  # type: ignore[arg-type]
        route="INT_STEAM",
        game_version="3.2.0",
        client=_client(),
    )
    assert result.changed is True
    assert result.sync_required is True


def test_existing_revision_rejects_catalog_hash_change() -> None:
    with pytest.raises(RevisionConflictError, match="catalog hash changed"):
        check_revision(
            _Connection((REVISION_ID, "0" * 32, object(), True)),  # type: ignore[arg-type]
            route="INT_STEAM",
            game_version="3.2.0",
            client=_client(),
        )


def test_writes_github_outputs() -> None:
    source = GameSource(
        route="INT_STEAM",
        version="3.2.0",
        revision="116",
        source_url="https://cdn.example/116",
        catalog_url="https://cdn.example/116/catalog_3.2.0.json",
    )
    stream = StringIO()
    write_github_output(
        RevisionCheck(
            source=source,
            catalog_hash="f" * 32,
            changed=True,
            sync_required=False,
            revision_id=str(REVISION_ID),
        ),
        stream,
    )
    text = stream.getvalue()
    assert "changed=true" in text
    assert "sync_required=false" in text
    assert f"revision_id={REVISION_ID}" in text
    assert "revision=116" in text
    assert f"catalog_hash={'f' * 32}" in text
