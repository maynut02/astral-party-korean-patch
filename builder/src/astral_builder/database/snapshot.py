from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from astral_builder.formats.model import SourceStrings


@dataclass(frozen=True, slots=True)
class SnapshotUnit:
    kind: str
    namespace: str
    key: str
    source: SourceStrings
    source_version_id: str
    translation: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.kind, self.namespace, self.key)

    @property
    def translated(self) -> bool:
        return bool(self.translation)

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "namespace": self.namespace,
            "key": self.key,
            "source": {
                "cn_s": self.source.cn_s,
                "en": self.source.en,
                "jp": self.source.jp,
                "cn_t": self.source.cn_t,
            },
            "sourceVersionId": self.source_version_id,
            "sourceFingerprint": self.source.fingerprint,
            "translation": self.translation,
        }


@dataclass(frozen=True, slots=True)
class TranslationSnapshot:
    revision_id: str
    route: str
    game_version: str
    revision: str
    locale: str
    units: tuple[SnapshotUnit, ...]

    @property
    def fingerprint(self) -> str:
        payload = {
            "revisionId": self.revision_id,
            "route": self.route,
            "gameVersion": self.game_version,
            "revision": self.revision,
            "locale": self.locale,
            "units": [unit.as_dict() for unit in self.units],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 2,
            "revisionId": self.revision_id,
            "route": self.route,
            "gameVersion": self.game_version,
            "revision": self.revision,
            "locale": self.locale,
            "fingerprint": self.fingerprint,
            "units": [unit.as_dict() for unit in self.units],
        }


def make_snapshot(
    *,
    revision_id: str,
    route: str,
    game_version: str,
    revision: str,
    locale: str,
    units: Iterable[SnapshotUnit],
) -> TranslationSnapshot:
    ordered = tuple(sorted(units, key=lambda unit: unit.identity))
    identities = [unit.identity for unit in ordered]
    if len(identities) != len(set(identities)):
        raise ValueError("snapshot contains duplicate translation identities")
    return TranslationSnapshot(
        revision_id=revision_id,
        route=route,
        game_version=game_version,
        revision=revision,
        locale=locale,
        units=ordered,
    )
