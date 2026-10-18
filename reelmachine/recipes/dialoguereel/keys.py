"""Find each key word inside its line, so the line can underline it."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .models import KeyWord
from .ruby import KANJI, parse_ruby, plain_text

_KANA = "぀-ゟ゠-ヿ"
_JAPANESE = re.compile(rf"[{_KANA}{KANJI}]")
_FINAL_KANA = re.compile(rf"[{_KANA}]$")


@dataclass(frozen=True)
class KeySpan:
    index: int   # which key
    start: int   # character offsets in the line's ruby-stripped plain text
    end: int


def _stem(text: str) -> str:
    if _JAPANESE.search(text):
        return text[:-1] if len(text) > 1 and _FINAL_KANA.search(text) else text
    low = text.lower()
    if len(low) >= 6:
        return low[:-2]
    if len(low) >= 4:
        return low[:-1]
    return low


def _find(plain: str, needle: str, taken: list[tuple[int, int]], latin: bool, whole: bool = False) -> tuple[int, int] | None:
    if not needle:
        return None
    if latin:
        pattern = re.compile(rf"(?<!\w){re.escape(needle)}" + (r"(?!\w)" if whole else ""), re.IGNORECASE)
    else:
        pattern = re.compile(re.escape(needle))
    for m in pattern.finditer(plain):
        if all(m.end() <= s or m.start() >= e for s, e in taken):
            return m.start(), m.end()
    return None


def match_keys(text: str, keys: Sequence[KeyWord]) -> list[KeySpan | None]:
    """One entry per key: its span in the plain text (exact text, else its stem), or None."""
    plain = plain_text(parse_ruby(text)[0])
    taken: list[tuple[int, int]] = []
    out: list[KeySpan | None] = []
    for i, key in enumerate(keys):
        latin = not _JAPANESE.search(key.text)
        hit = _find(plain, key.text, taken, latin, whole=True)
        if hit is None:
            stem = _stem(key.text)
            if stem != key.text:
                hit = _find(plain, stem, taken, latin)
        if hit is None:
            out.append(None)
        else:
            taken.append(hit)
            out.append(KeySpan(i, *hit))
    return out
