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
    """One caption unit: a word, or a character for an unspaced script. `accent` is a whole-word accent;
    `accent_spans` are (start, end) character offsets of the accented runs in a partly accented word
    (`*R*etrieval` stays one word on screen, its R coloured)."""

    text: str
    accent: bool
    accent_spans: tuple[tuple[int, int], ...] = ()


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
    word: list[tuple[str, bool]] = []
    for ch, on in [*_marked(caption), (" ", False)]:
        if not ch.isspace():
            word.append((ch, on))
        elif word:
            out.append(_word(word))
            word = []
    return out


def _word(chars: list[tuple[str, bool]]) -> Unit:
    """A spaced-script word from its marked characters: a whole-word accent when every letter is accented
    (punctuation outside the stars, as in `*ChatGPT*,`, does not count), else the accented runs as spans."""
    text = "".join(ch for ch, _ in chars)
    letters = [on for ch, on in chars if not unicodedata.category(ch).startswith("P")] or [on for _, on in chars]
    if all(letters):
        return Unit(text, True)
    spans: list[tuple[int, int]] = []
    for i, (_, on) in enumerate(chars):
        if not on:
            continue
        if spans and spans[-1][1] == i:
            spans[-1] = (spans[-1][0], i + 1)
        else:
            spans.append((i, i + 1))
    return Unit(text, False, tuple(spans))


def plain(text: str) -> str:
    return text.replace("*", "")


def norm(token: str) -> str:
    """A token reduced to what the aligner compares: casefolded, without `*` or punctuation."""
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


def merge_short(
    chunks: list[list[int]], times: list[tuple[int, int]], min_ms: int = 350, max_units: int = 8,
    us: list[Unit] | None = None, max_chars: int = 18,
) -> list[list[int]]:
    """Chunks with none shorter than `min_ms` unless a merge would break the cap: a short chunk (its time on screen, up to the next chunk's first word) joins the
    next one of its line (the previous when it is last). The cap is `max_units` words, or `max_chars`
    characters when `us` is given (unspaced scripts)."""

    def fits(a: list[int], b: list[int]) -> bool:
        if us is not None:
            return sum(len(us[i].text) for i in a + b) <= max_chars
        return len(a) + len(b) <= max_units

    def short(c: list[int], nxt: list[int] | None) -> bool:
        # on screen from its first word until the next chunk replaces it (the line's end for the last)
        end = times[nxt[0]][0] if nxt else times[-1][1]
        return end - times[c[0]][0] < min_ms

    out = [list(c) for c in chunks]
    i = 0
    while i < len(out):
        if i + 1 < len(out) and short(out[i], out[i + 1]) and fits(out[i], out[i + 1]):
            out[i] += out.pop(i + 1)
        else:
            i += 1
    # a short last chunk has no next to join: fold it into the previous
    while len(out) > 1 and short(out[-1], None) and fits(out[-2], out[-1]):
        last = out.pop()
        out[-1] += last
    return out
