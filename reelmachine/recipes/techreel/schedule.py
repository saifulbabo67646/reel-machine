"""Beat schedule from per-line speech lengths, and the cue/exit times visuals hang on."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from reelmachine.recipes.explainer3d.timing import estimate_speech_ms

from .markup import Unit, norm
from .models import Beat, Cue, TechReelInputs

LINE_GAP_MS = 120
TRANSITION_MS = {"cut": 150, "slide": 250, "flash": 200, "zoom": 300}
OUTRO_MS = 1200
_MIN_SPEECH_MS = 400


@dataclass(frozen=True)
class LineSlot:
    start_ms: int
    speech_ms: int
    end_ms: int


@dataclass(frozen=True)
class BeatSlot:
    start_ms: int
    end_ms: int
    lines: tuple[LineSlot, ...]


@dataclass(frozen=True)
class Plan:
    beats: tuple[BeatSlot, ...]
    duration_ms: int


def schedule(beats: Sequence[Beat], speech_ms: Sequence[Sequence[int]]) -> Plan:
    slots: list[BeatSlot] = []
    for i, (beat, speeches) in enumerate(zip(beats, speech_ms, strict=True)):
        start = 0 if i == 0 else slots[-1].end_ms + TRANSITION_MS[beat.transition]
        t = start
        lines: list[LineSlot] = []
        for j, ms in enumerate(speeches):
            if j:
                t += LINE_GAP_MS
            lines.append(LineSlot(t, ms, t + ms))
            t += ms
        slots.append(BeatSlot(start, t, tuple(lines)))
    return Plan(tuple(slots), (slots[-1].end_ms if slots else 0) + OUTRO_MS)


def estimate(inputs: TechReelInputs) -> Plan:
    """A plan from text length alone, for storyboards before any voice exists."""
    speech = [
        [max(_MIN_SPEECH_MS, estimate_speech_ms(ln.say, inputs.language)) for ln in b.lines]
        for b in inputs.beats
    ]
    return schedule(inputs.beats, speech)


def word_index(cue: Cue, us: Sequence[Unit]) -> int | None:
    """Index of the first unit matching the cue word's first token; the one matcher lint and cue_ms share.
    Failing a single unit, the first run of consecutive units whose texts joined match it: in an unspaced
    script the units are characters, so "パラメータ" is a run of five."""
    tokens = (cue.word or "").split()
    want = norm(tokens[0]) if tokens else ""
    if not want:
        return None
    keys = [norm(u.text) for u in us]
    hit = next((k for k, key in enumerate(keys) if key == want), None)
    if hit is not None:
        return hit
    for k, key in enumerate(keys):
        if not key or not want.startswith(key):
            continue
        run = ""
        for nxt in keys[k:]:
            run += nxt
            if run == want:
                return k
            if not want.startswith(run):
                break
    return None


def cue_ms(
    cue: Cue | None,
    beat: BeatSlot,
    times: Sequence[Sequence[tuple[int, int]]],
    us: Sequence[Sequence[Unit]],
) -> tuple[int, bool]:
    """Absolute ms of a cue, and whether it resolved as asked (else it fell back)."""
    if cue is None:
        return beat.start_ms, True
    if cue.line >= len(beat.lines):
        return beat.start_ms, False
    line = beat.lines[cue.line]
    if cue.word is not None:
        k = word_index(cue, us[cue.line])
        if k is not None and k < len(times[cue.line]):
            return line.start_ms + times[cue.line][k][0], True
        return line.start_ms, False
    if cue.frac is not None:
        return line.start_ms + round(cue.frac * line.speech_ms), True
    return line.start_ms, True


def exit_ms(
    exit: Cue | Literal["beat"],
    beat: BeatSlot,
    next_start: int | None,
    times: Sequence[Sequence[tuple[int, int]]],
    us: Sequence[Sequence[Unit]],
) -> int:
    """When a visual leaves; an unresolved exit cue falls back like cue_ms (beat/line start)."""
    if exit == "beat":
        return next_start if next_start is not None else beat.end_ms + OUTRO_MS
    return cue_ms(exit, beat, times, us)[0]
