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
_ONE_KANJI = re.compile(rf"[{KANJI}]")
MIN_STEM = 2   # a shorter stem (one kana, one letter) would match a particle or any word's first sound


@dataclass(frozen=True)
class KeySpan:
    index: int   # which key
    start: int   # character offsets in the line's ruby-stripped plain text
    end: int


def _stem(text: str) -> str | None:
    """The part of the key a conjugated form keeps, or None when that part would be too short to place safely.
    A lone kanji is still a safe stem (飲 for 飲む): it carries the word's meaning, unlike a lone kana."""
    if _JAPANESE.search(text):
        if len(text) <= 1 or not _FINAL_KANA.search(text):
            return text
        stem = text[:-1]
        return stem if len(stem) >= MIN_STEM or _ONE_KANJI.fullmatch(stem) else None
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
        text = plain_text(parse_ruby(key.text)[0])   # a key may carry ruby markup too
        latin = not _JAPANESE.search(text)
        hit = _find(plain, text, taken, latin, whole=True)
        if hit is None:
            stem = _stem(text)
            if stem is not None and stem != text:
                hit = _find(plain, stem, taken, latin)
        if hit is None:
            out.append(None)
        else:
            taken.append(hit)
            out.append(KeySpan(i, *hit))
    return out
