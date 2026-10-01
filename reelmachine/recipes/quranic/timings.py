"""Word timings: the provider's when it has them, a deterministic fallback otherwise.

The fallback is proportional to the visible length of each word (Arabic diacritics do not
count), split across the ayah's real audio window — deterministic, and honest: the
manifest records which source was used.
"""

from __future__ import annotations

from ...core.timeline import WordSpan
from ...core.timing import proportional_word_spans, visible_length  # noqa: F401
from .models import AyahMaterial


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
