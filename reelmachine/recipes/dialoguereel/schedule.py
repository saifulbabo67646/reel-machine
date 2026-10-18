"""Where each spoken line sits on the reel's timeline: a hook, then the lines with gaps, then a tail."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..explainer3d.timing import estimate_speech_ms
from .models import DialogueReelInputs
from .ruby import parse_ruby, plain_text

HOOK_MS = 1500
GAP_MS = 350
TAIL_MS = 900
MEANING_AT = 0.6   # the translation appears this far through the spoken line


@dataclass(frozen=True)
class LineWindow:
    start_ms: int
    speech_ms: int
    end_ms: int
    meaning_ms: int


@dataclass(frozen=True)
class Schedule:
    hook_ms: int
    lines: tuple[LineWindow, ...]
    duration_ms: int


def schedule(speech_ms: Sequence[int]) -> Schedule:
    windows: list[LineWindow] = []
    t = HOOK_MS
    for i, speech in enumerate(speech_ms):
        if i:
            t += GAP_MS
        windows.append(LineWindow(t, speech, t + speech, t + round(MEANING_AT * speech)))
        t += speech
    return Schedule(HOOK_MS, tuple(windows), t + TAIL_MS)


def estimate(inputs: DialogueReelInputs) -> Schedule:
    return schedule([
        estimate_speech_ms(plain_text(parse_ruby(line.text)[0]), inputs.language) for line in inputs.lines
    ])
