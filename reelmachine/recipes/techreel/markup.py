"""Accent markup and caption units: `*…*` marks accent words; captions split into words (or characters)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

_UNSPACED = {"ja", "zh", "th"}
_SPACED_BREAK = ".,!?;:।…"
_UNSPACED_BREAK = "。、！？"
_MAX_WORDS = 6
_MAX_CHARS = 14


@dataclass(frozen=True)
class Unit:
    """One caption unit: a word, or a character for an unspaced script."""

    text: str
    accent: bool


def is_unspaced(language: str) -> bool:
    return language.split("-")[0].lower() in _UNSPACED


def _marked(caption: str) -> list[tuple[str, bool]]:
    """Each character with its accent flag; `*` pairs toggle, a lone trailing `*` stays literal."""
    stars = [i for i, ch in enumerate(caption) if ch == "*"]
    paired = set(stars[: len(stars) - len(stars) % 2])   # an unclosed `*` is literal
    out: list[tuple[str, bool]] = []
    on = False
    for i, ch in enumerate(caption):
        if i in paired:
            on = not on
        else:
            out.append((ch, on))
    return out


def units(caption: str, language: str) -> list[Unit]:
    out: list[Unit] = []
    if is_unspaced(language):
        for ch, on in _marked(caption):
            if ch.isspace():
                continue
            if out and unicodedata.category(ch).startswith("P"):
                out[-1] = Unit(out[-1].text + ch, out[-1].accent)   # punctuation rides on the preceding unit
            else:
                out.append(Unit(ch, on))
        return out
    word, accent = "", False
    for ch, on in [*_marked(caption), (" ", False)]:
        if ch.isspace():
            if word:
                out.append(Unit(word, accent))
            word, accent = "", False
        else:
            word += ch
            accent = accent or on
    return out


def plain(text: str) -> str:
    return text.replace("*", "")


def norm(token: str) -> str:
    """A token reduced to what the aligner compares: casefolded, no accent marks, no punctuation."""
    return "".join(ch for ch in token.casefold() if ch != "*" and not unicodedata.category(ch).startswith("P"))


def chunks(us: list[Unit], language: str) -> list[list[int]]:
    """Caption chunks as unit indices: at most 6 words (14 characters unspaced), broken at punctuation."""
    out: list[list[int]] = []
    cur: list[int] = []
    size = 0
    unspaced = is_unspaced(language)
    for i, u in enumerate(us):
        if unspaced and cur and size + len(u.text) > _MAX_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(u.text)
        if unspaced:
            stop = u.text[-1] in _UNSPACED_BREAK
        else:
            stop = len(cur) >= _MAX_WORDS or (len(cur) >= 2 and u.text[-1] in _SPACED_BREAK)
        if stop:
            out.append(cur)
            cur, size = [], 0
    if cur:
        out.append(cur)
    return out
