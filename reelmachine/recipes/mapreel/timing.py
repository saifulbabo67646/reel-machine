"""Things pop when they are said: names are matched against the voice's word timings."""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence

from ...core.timeline import WordSpan

FLY_MS = 900          # the camera's flight at the start of a segment
POP_GAP_MS = 250      # never two pops closer than this
MIN_SEGMENT_MS = 2200


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def _stream(words: Sequence[WordSpan]) -> tuple[list[str], list[int]]:
    flat, owner = [], []
    for index, word in enumerate(words):
        for tok in tokens(word.text):
            flat.append(tok)
            owner.append(index)
    return flat, owner


def _find(flat: list[str], phrase: list[str], start: int) -> int | None:
    k = len(phrase)
    for i in range(start, len(flat) - k + 1):
        if flat[i:i + k] == phrase:
            return i
    return None


def match(phrases: Sequence[str], words: Sequence[WordSpan]) -> list[int | None]:
    flat, owner = _stream(words)
    out: list[int | None] = []
    cursor = 0
    for phrase in phrases:
        toks = tokens(phrase)
        if not toks:
            out.append(None)
            continue
        hit = _find(flat, toks, cursor)
        if hit is None:
            hit = _find(flat, toks, 0)  # said earlier than listed
        else:
            cursor = hit + len(toks)
        out.append(None if hit is None else words[owner[hit]].start_ms)
    return out


def pop_times(spoken: Sequence[int | None], *, overrides: Sequence[int | None],
              speech_start_ms: int, speech_end_ms: int) -> tuple[list[int], list[int]]:
    """Segment-relative pop times. Spoken times are offset by `speech_start_ms`; an override
    is already segment-relative. Unmatched entries spread across the gaps; nothing pops
    before the camera lands; pops keep `POP_GAP_MS` apart."""
    n = len(spoken)
    raw: list[int | None] = []
    for said, forced in zip(spoken, overrides):
        raw.append(forced if forced is not None else (None if said is None else speech_start_ms + said))
    spread = [i for i, (said, forced) in enumerate(zip(spoken, overrides)) if said is None and forced is None]
    floor = FLY_MS - 100
    known = list(raw)
    for i in spread:  # a run of unmatched entries spreads evenly between its known neighbours
        lo = next((j for j in range(i - 1, -1, -1) if known[j] is not None), -1)
        hi = next((j for j in range(i + 1, n) if known[j] is not None), n)
        prev = known[lo] if lo >= 0 else floor
        nxt = known[hi] if hi < n else speech_end_ms
        raw[i] = int(prev + (nxt - prev) * (i - lo) / (hi - lo))
    times: list[int] = []
    last = floor - POP_GAP_MS
    for value in raw:
        t = max(int(value), floor, last + POP_GAP_MS)
        times.append(t)
        last = t
    return times, spread
