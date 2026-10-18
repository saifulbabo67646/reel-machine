"""Narration markup: what is said and what is shown can differ.

`*word*` highlights a caption word (several words inside one pair are each highlighted);
`[shown|spoken]` puts `shown` on screen while the voice says `spoken` — `[$1|one dollar]`,
`[WATCH 👀|watch]`. Everything else is a plain word, shown as spoken. The voice reads
`spoken_text`; each shown token takes the timings of the spoken words it stands for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ...core.timeline import WordSpan

_PIECE = re.compile(r"\[([^\]|]*)\|([^\]]*)\]|\S+")


@dataclass(frozen=True)
class Token:
    shown: str
    spoken: str
    hot: bool = False

    @property
    def spoken_words(self) -> int:
        return len(self.spoken.split())


def _hot(text: str) -> tuple[str, bool]:
    if len(text) > 2 and text.startswith("*") and text.endswith("*"):
        return text[1:-1], True
    return text, False


def parse_markup(narration: str) -> list[Token]:
    tokens: list[Token] = []
    hot_run = False  # inside a *multi word* span
    for match in _PIECE.finditer(narration or ""):
        if match.group(1) is not None:
            shown, hot = _hot(match.group(1).strip())
            tokens.append(Token(shown=shown, spoken=match.group(2).strip(), hot=hot))
            continue
        word = match.group(0)
        trail = re.search(r"[.,!?;:…]*$", word).group(0)  # `*potato*.` closes before its full stop
        core = word[: len(word) - len(trail)] if trail else word
        opens, closes = core.startswith("*"), core.endswith("*") and len(core) > 1
        clean = core.strip("*") + trail
        if not core.strip("*"):
            continue
        hot = hot_run or opens
        if opens and not closes:
            hot_run = True
        elif closes:
            hot_run = False
        tokens.append(Token(shown=clean, spoken=clean, hot=hot))
    return tokens


def spoken_text(tokens: Sequence[Token]) -> str:
    return " ".join(t.spoken for t in tokens if t.spoken)


def display_text(tokens: Sequence[Token]) -> str:
    return " ".join(t.shown for t in tokens if t.shown)


@dataclass(frozen=True)
class ShownWord:
    text: str
    start_ms: int
    end_ms: int
    hot: bool = False


def display_words(tokens: Sequence[Token], spans: Sequence[WordSpan], *, total_ms: int) -> list[ShownWord]:
    """Shown tokens with times. When the voice's words line up with the spoken words,
    each token spans its own words; otherwise tokens share the line evenly."""
    shown = [t for t in tokens if t.shown]
    needed = sum(t.spoken_words for t in tokens)
    out: list[ShownWord] = []
    if spans and len(spans) == needed:
        i = 0
        last_end = 0
        for t in tokens:
            n = t.spoken_words
            if n == 0:  # shown only: appears with the word before it
                if t.shown:
                    out.append(ShownWord(t.shown, last_end, last_end, t.hot))
                continue
            first, last = spans[i], spans[i + n - 1]
            i += n
            last_end = last.end_ms
            if t.shown:
                out.append(ShownWord(t.shown, first.start_ms, last.end_ms, t.hot))
        return out
    if not shown:
        return []
    start = spans[0].start_ms if spans else 0
    end = spans[-1].end_ms if spans else total_ms
    step = (end - start) / len(shown)
    return [ShownWord(t.shown, int(round(start + k * step)), int(round(start + (k + 1) * step)), t.hot)
            for k, t in enumerate(shown)]
