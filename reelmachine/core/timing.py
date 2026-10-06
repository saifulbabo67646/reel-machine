"""Timing helpers shared by recipes that build captions or schedules from words.

Quranic uses them when a reciter has no word timings; doodle uses them when a voice
provider reports only line-level timings (Cartesia's HTTP route does).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .timeline import WordSpan

_DIACRITICS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")


def visible_length(word: str) -> int:
    """Letters that actually take time: diacritics and elongation marks do not."""
    return max(1, len(_DIACRITICS.sub("", word).strip()))


def proportional_word_spans(
    words: Sequence[str],
    start_ms: int,
    end_ms: int,
) -> list[WordSpan]:
    """Split `[start_ms, end_ms]` across `words` by visible length, deterministically."""
    if not words:
        return []
    weights = [visible_length(word) for word in words]
    total = float(sum(weights))
    spans: list[WordSpan] = []
    cursor = float(start_ms)
    window = max(0, end_ms - start_ms)
    for word, weight in zip(words, weights):
        share = window * weight / total
        word_start = int(round(cursor))
        word_end = int(round(cursor + share))
        spans.append(WordSpan(text=word, start_ms=word_start, end_ms=max(word_start + 1, word_end)))
        cursor += share
    spans[-1] = spans[-1].model_copy(update={"end_ms": max(spans[-1].start_ms + 1, end_ms)})
    return spans
