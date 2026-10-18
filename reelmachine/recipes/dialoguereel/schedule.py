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


BRIDGE_MS = 700
SLOW_RATE = 0.75   # a slow replay is spoken at this fraction of normal speed
RECAP_MS = 3000
QUIZ_COUNT_MS = 3000
QUIZ_HOLD_MS = 800


@dataclass(frozen=True)
class LineWindow:
    start_ms: int
    speech_ms: int
    end_ms: int
    meaning_ms: int
    slow_start_ms: int | None = None
    slow_ms: int = 0


@dataclass(frozen=True)
class Cue:
    """When a line's audio starts, and at what rate it is played."""

    at_ms: int
    line: int
    rate: float


@dataclass(frozen=True)
class QuizWindow:
    line: int
    start_ms: int
    reveal_ms: int
    end_ms: int


@dataclass(frozen=True)
class Schedule:
    hook_ms: int
    lines: tuple[LineWindow, ...]
    duration_ms: int
    listen: tuple[LineWindow, ...] = ()
    bridge_ms: int | None = None
    recap_ms: int | None = None
    quiz: QuizWindow | None = None
    cues: tuple[Cue, ...] = ()


def _window(start: int, speech: int) -> LineWindow:
    return LineWindow(start, speech, start + speech, start + round(MEANING_AT * speech))


def schedule(speech_ms: Sequence[int], *, slow: Sequence[bool] = (), listen_first: bool = False,
             recap: bool = False, quiz_line: int | None = None) -> Schedule:
    cues: list[Cue] = []
    listen: list[LineWindow] = []
    bridge: int | None = None
    t = HOOK_MS
    if listen_first and speech_ms:
        for i, speech in enumerate(speech_ms):
            if i:
                t += GAP_MS
            listen.append(_window(t, speech))
            cues.append(Cue(t, i, 1.0))
            t += speech
        bridge = t + GAP_MS
        t = bridge + BRIDGE_MS
    windows: list[LineWindow] = []
    for i, speech in enumerate(speech_ms):
        if i:
            t += GAP_MS
        window = _window(t, speech)
        cues.append(Cue(t, i, 1.0))
        t += speech
        if i < len(slow) and slow[i]:
            slow_start, slow_len = t + GAP_MS, round(speech / SLOW_RATE)
            window = LineWindow(window.start_ms, speech, window.end_ms, window.meaning_ms, slow_start, slow_len)
            cues.append(Cue(slow_start, i, SLOW_RATE))
            t = slow_start + slow_len
        windows.append(window)
    recap_ms = None
    if recap and windows:
        recap_ms = t + GAP_MS
        t = recap_ms + RECAP_MS
    quiz = None
    if quiz_line is not None and 0 <= quiz_line < len(windows):
        start = t if recap_ms is not None else t + GAP_MS
        reveal = start + QUIZ_COUNT_MS
        quiz = QuizWindow(quiz_line, start, reveal, reveal + speech_ms[quiz_line] + QUIZ_HOLD_MS)
        cues.append(Cue(reveal, quiz_line, 1.0))
        t = quiz.end_ms
    return Schedule(HOOK_MS, tuple(windows), t + TAIL_MS, tuple(listen), bridge, recap_ms, quiz, tuple(cues))


def quiz_line_for(inputs: DialogueReelInputs) -> int | None:
    """Which line the quiz asks about: as given, or the one with most key words, else the longest meaning."""
    if inputs.quiz is False or not inputs.lines:
        return None
    if inputs.quiz is not True:
        return int(inputs.quiz)
    keys = [len(line.keys) for line in inputs.lines]
    if max(keys) > 0:
        return keys.index(max(keys))
    meanings = [len(line.meaning) for line in inputs.lines]
    return meanings.index(max(meanings))


def estimate(inputs: DialogueReelInputs) -> Schedule:
    return schedule(
        [estimate_speech_ms(plain_text(parse_ruby(line.text)[0]), inputs.language) for line in inputs.lines],
        slow=[line.slow for line in inputs.lines],
        listen_first=inputs.flow == "listen_first",
        recap=any(line.keys for line in inputs.lines),
        quiz_line=quiz_line_for(inputs),
    )
