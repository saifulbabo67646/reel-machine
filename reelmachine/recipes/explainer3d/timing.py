"""Narration sets the clock: a shot lasts as long as its line plus padding."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

LEAD_MS = 250
TAIL_MS = 350
MIN_SHOT_MS = 1500
XFADE_MS = 300

_WORDS_PER_S = 2.6
_CJK_CHARS_PER_S = 7.0
_READING = re.compile(r"\[[^\]]*\]")


@dataclass(frozen=True)
class ShotWindow:
    index: int
    start_ms: int
    duration_ms: int
    speech_offset_ms: int
    speech_ms: int

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


def shot_windows(
    speech_ms: Sequence[int],
    *,
    durations: Sequence[int | None] | None = None,
    lead_ms: int = LEAD_MS,
    tail_ms: int = TAIL_MS,
    min_ms: int = MIN_SHOT_MS,
) -> list[ShotWindow]:
    """A shot lasts its line plus padding (never under `min_ms`), or at least its own
    `duration` when it has one; a beat shot with no line lasts exactly its duration."""
    durations = list(durations) if durations is not None else [None] * len(speech_ms)
    windows: list[ShotWindow] = []
    cursor = 0
    for index, (ms, fixed) in enumerate(zip(speech_ms, durations)):
        if int(ms) <= 0:
            duration, lead = int(fixed or min_ms), 0
        else:
            duration, lead = max(min_ms, lead_ms + int(ms) + tail_ms, int(fixed or 0)), lead_ms
        windows.append(ShotWindow(index, cursor, duration, lead, int(ms)))
        cursor += duration
    return windows


MAX_STRETCH = 1.6


def fit_windows(windows: Sequence[ShotWindow], target_ms: int) -> list[ShotWindow]:
    """Lengthen shot tails, in proportion to each shot, until the reel reaches
    `target_ms` — at most 1.6x per shot. A reel already long enough is left alone."""
    total = sum(w.duration_ms for w in windows)
    if not windows or total >= target_ms:
        return list(windows)
    ratio = min(MAX_STRETCH, target_ms / total)
    durations = [int(w.duration_ms * ratio) for w in windows]
    short = min(int(target_ms), int(total * MAX_STRETCH)) - sum(durations)
    durations[-1] += max(0, short)  # rounding lands on the target exactly
    out, cursor = [], 0
    for w, d in zip(windows, durations):
        out.append(ShotWindow(w.index, cursor, d, w.speech_offset_ms, w.speech_ms))
        cursor += d
    return out


def auto_layout(count: int, lo: float = 0.1, hi: float = 0.95) -> list[tuple[float, float]]:
    if count <= 0:
        return []
    step = (hi - lo) / count
    return [(round(lo + i * step, 6), round(lo + (i + 1) * step, 6)) for i in range(count)]


def estimate_speech_ms(text: str, language: str) -> int:
    if language.split("-")[0].lower() in {"ja", "zh", "ko"}:
        chars = len(re.sub(r"\s", "", _READING.sub("", text)))
        return int(chars / _CJK_CHARS_PER_S * 1000)
    return int(len(text.split()) / _WORDS_PER_S * 1000)
