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


def shot_windows(speech_ms: Sequence[int]) -> list[ShotWindow]:
    windows: list[ShotWindow] = []
    cursor = 0
    for index, ms in enumerate(speech_ms):
        duration = max(MIN_SHOT_MS, LEAD_MS + int(ms) + TAIL_MS)
        windows.append(ShotWindow(index, cursor, duration, LEAD_MS, int(ms)))
        cursor += duration
    return windows


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
