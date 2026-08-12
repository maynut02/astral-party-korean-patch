from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class TranslationKind(StrEnum):
    LANG = "lang"
    STR = "str"


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True)
class SourceStrings:
    cn_s: str = ""
    en: str = ""
    jp: str = ""
    cn_t: str = ""

    def normalized(self) -> SourceStrings:
        return SourceStrings(
            cn_s=normalize_text(self.cn_s),
            en=normalize_text(self.en),
            jp=normalize_text(self.jp),
            cn_t=normalize_text(self.cn_t),
        )

    @property
    def fingerprint(self) -> str:
        value = self.normalized()
        payload = "\0".join((value.cn_s, value.en, value.jp, value.cn_t)).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class TranslationUnit:
    kind: TranslationKind
    namespace: str
    key: str
    source: SourceStrings

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.kind.value, self.namespace, self.key)
