"""Word timings: the provider's when it has them, a deterministic fallback otherwise.

The fallback is proportional to the visible length of each word (Arabic diacritics do not
count), split across the ayah's real audio window — deterministic, and honest: the
manifest records which source was used.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ...core.timeline import WordSpan
from .models import AyahMaterial

_DIACRITICS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")


def visible_length(word: str) -> int:
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


def timings_for_ayah(
    ayah: AyahMaterial,
    start_ms: int,
    end_ms: int,
) -> tuple[list[WordSpan], bool]:
    """Return `(word spans, used_provider_timings)` for one ayah on the global timeline."""
    if ayah.word_timings and len(ayah.word_timings) == max(1, len(ayah.words)):
        offset = start_ms
        spans = [
            WordSpan(
                text=span.text,
                start_ms=offset + span.start_ms,
                end_ms=offset + span.end_ms,
            )
            for span in ayah.word_timings
        ]
        return spans, True
    return proportional_word_spans(ayah.words, start_ms, end_ms), False
