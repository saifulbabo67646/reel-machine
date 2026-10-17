"""The hook lint: retention rules a map reel is checked against before it is built.

Rules 1 and 2 (`weak-opener`, `unpaid-loop`) are errors in a strict build; every other finding is a warning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from ..explainer3d.markup import parse_markup, spoken_text
from ..explainer3d.timing import ShotWindow, shot_windows
from .kinds import RANK_AFTER_FILL_MS, VS_AT_MS
from .models import MapReelInputs
from .timing import FLY_MS, MIN_SEGMENT_MS, pop_times

HOOK_WINDOW_WORDS = 7         # about the first 2 s of speech
BEAT_GAP_S = 1.2
REHOOK_GAP_S = 12.0
MONOTONY_RUN = 4
NUMBER_WORDS = r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|hundred|thousand|million|billion|first|last|half"
HOOK_SIGNAL = re.compile(
    rf"\d|\?|\b(you|your|you're|only|never|nobody|no one|secret|surprise\w*|guess|wait|actually|why|how|"
    rf"what|which|most|least|biggest|smallest|richest|poorest|best|worst|weirdest|\w+est|{NUMBER_WORDS})\b", re.I)
WEAK_START = re.compile(r"^(welcome|today|hi|hello|hey guys|in this video|this is|let's talk)\b|^[\w .'-]{1,30}\s(has|have|is home to)\b", re.I)
TEASE = re.compile(r"\b(number (one|1)|no\.? ?1|the last one|surprise\w*|guess|wait (for|till|until)|stay (until|till)|at the end|the answer)\b", re.I)
PAYOFF = re.compile(r"\b(number (one|1)|no\.? ?1|finally|the answer|the winner|and the last|last but not least|drumroll)\b", re.I)
REHOOK = re.compile(r"\b(but|wait|here's|now|guess|surprising\w*|plot twist|believe it or not|number \w+|even|actually|meanwhile|and the)\b|\?", re.I)
CTA = re.compile(r"\b(comment|comments|follow|share|save|subscribe|tell me|let me know|which|what|would you|did i miss)\b|\?", re.I)

REHOOK_KINDS = ("rank", "versus", "highlight")
LONG_REEL_S = 75
PADDING_SHARE = 0.85


@dataclass
class Finding:
    rule: str
    severity: Literal["error", "warning"]
    message: str
    hint: str
    segment: str | None = None


def _text(segment: Any) -> str:
    return spoken_text(parse_markup(segment.narration))


def _at(text: str, phrases: Sequence[str]) -> float | None:
    """Where in the text (0..1) the earliest of `phrases` is said, as whole words."""
    best = None
    for phrase in phrases:
        if not phrase.strip():
            continue
        m = re.search(rf"(?<!\w){re.escape(phrase.strip())}(?!\w)", text, re.I)
        if m and (best is None or m.start() < best):
            best = m.start()
    return None if best is None else best / max(1, len(text))


def _country_phrases(index: int, j: int, query: str, names: Mapping[str, Sequence[str]] | None) -> list[str]:
    """What the voice may say for a highlight or versus country: its names when known, else what was written."""
    return list(names.get(f"seg-{index + 1}/{j}", ())) if names and f"seg-{index + 1}/{j}" in names else [query]


def _entries(segment: Any, index: int = 0,
             names: Mapping[str, Sequence[str]] | None = None) -> list[tuple[list[str], float | None]]:
    """What the segment pops or fills, in the order it is written: the phrases that name it, and a forced time.
    A versus country lists its stat's value first, then its names, as the runtime does."""
    def item(it: Any) -> tuple[list[str], float | None]:
        return [it.say or it.name], it.at

    kind = segment.kind
    if kind == "country":
        return [item(i) for i in segment.items]
    if kind == "highlight":
        return [(_country_phrases(index, j, c, names), None) for j, c in enumerate(segment.countries)]
    if kind == "rank":
        stat = [([segment.stat.value], None)] if segment.stat else []
        return [*stat, *(item(i) for i in segment.items)]
    if kind == "versus":
        return [([stat.value, *_country_phrases(index, j, c, names)], None)
                for j, (c, stat) in enumerate(zip(segment.countries, segment.stats))]
    out: list[tuple[list[str], float | None]] = []
    for entry in segment.regions:
        out.append(([entry.say or entry.region], None))
        out.extend(item(i) for i in entry.items)
    return out


def estimate_beats(inputs: MapReelInputs, windows: Sequence[ShotWindow], *,
                   names: Mapping[str, Sequence[str]] | None = None) -> list[list[float]]:
    """Reel-time seconds of everything that happens on screen, per segment, sorted. `names` maps
    "seg-N/j" (country j of a highlight or versus) to what the voice says for it."""
    out = []
    for index, (segment, window) in enumerate(zip(inputs.segments, windows)):
        text = _text(segment)
        entries = _entries(segment, index, names)
        start, off, speech = window.start_ms, window.speech_offset_ms, window.speech_ms
        spoken = [None if (f := _at(text, p)) is None else int(f * speech) for p, _ in entries]
        forced = [None if at is None else int(at * 1000) for _, at in entries]
        times, _ = pop_times(spoken, overrides=forced, speech_start_ms=off, speech_end_ms=off + speech)
        beats = list(times)
        if not segment.camera.hold:
            beats.append(0)
        if segment.kind in ("country", "rank", "regions"):
            beats.append(FLY_MS)
        if segment.kind == "rank":
            badge = FLY_MS + RANK_AFTER_FILL_MS
            beats.append(badge)
            if segment.stat:  # a value said before the badge lands waits for it
                beats[0] = max(beats[0], badge)
        if segment.kind == "versus":
            beats.append(VS_AT_MS)
        out.append(sorted((start + ms) / 1000 for ms in beats))
    return out


def _rule_beat_gap(windows: Sequence[ShotWindow], beats: list[list[float]]) -> list[Finding]:
    found = []
    for i, (w, seg_beats) in enumerate(zip(windows, beats)):
        lo = (w.start_ms + w.speech_offset_ms) / 1000
        hi = lo + w.speech_ms / 1000
        points = sorted([lo, hi, *(b for b in seg_beats if lo <= b <= hi)])
        gap, at = max(((b - a, a) for a, b in zip(points, points[1:])), default=(0.0, lo))
        if gap > BEAT_GAP_S:
            found.append(Finding(
                "beat-gap", "warning",
                f"nothing appears on screen for {gap:.1f} s from {at:.1f} s (limit {BEAT_GAP_S} s)",
                "name something sooner, add an item, or split the line into two segments",
                f"seg-{i + 1}"))
    return found


def _rule_rehook(inputs: MapReelInputs, windows: Sequence[ShotWindow]) -> list[Finding]:
    times = [0.0, windows[0].end_ms / 1000]
    for i, (segment, w) in enumerate(zip(inputs.segments, windows)):
        if i and segment.kind in REHOOK_KINDS:
            times.append(w.start_ms / 1000)
        text = _text(segment)
        for m in REHOOK.finditer(text):
            times.append((w.start_ms + w.speech_offset_ms + m.start() / max(1, len(text)) * w.speech_ms) / 1000)
    end = windows[-1].end_ms / 1000
    times = sorted({*times, end})
    return [Finding("no-rehook", "warning",
                    f"no new hook from {a:.0f} s to {b:.0f} s ({b - a:.0f} s; limit {REHOOK_GAP_S:.0f} s)",
                    "add a rank, versus or highlight segment, or a line like 'but wait' or 'now guess which'")
            for a, b in zip(times, times[1:]) if b - a > REHOOK_GAP_S]


def _rule_monotony(inputs: MapReelInputs) -> list[Finding]:
    found, run = [], 0
    for i, segment in enumerate([*inputs.segments, None]):
        if segment is not None and segment.kind == "country":
            run += 1
            continue
        if run >= MONOTONY_RUN:
            found.append(Finding("monotony", "warning", f"{run} country segments in a row (from seg-{i - run + 1})",
                                 "put a rank, versus or highlight segment between them",
                                 f"seg-{i - run + 1}"))
        run = 0
    return found


def _rule_length(inputs: MapReelInputs, windows: Sequence[ShotWindow]) -> list[Finding]:
    # the windows may have been stretched to `length`; the script's own pace is what the lint judges
    estimated = shot_windows([w.speech_ms for w in windows], min_ms=MIN_SEGMENT_MS)[-1].end_ms / 1000
    found = []
    if inputs.length and estimated < PADDING_SHARE * inputs.length:
        found.append(Finding("length", "warning",
                             f"the script reads in about {estimated:.0f} s, so a {inputs.length} s target means padding",
                             f"set length to about {round(estimated)} or write more"))
    if estimated > LONG_REEL_S:
        found.append(Finding("length", "warning",
                             f"about {estimated:.0f} s estimated; most map reels stay under 60 s",
                             "cut segments or tighten the narration"))
    return found


def _rule_unsourced(inputs: MapReelInputs) -> list[Finding]:
    names = []
    for i, segment in enumerate(inputs.segments):
        if segment.source:
            continue
        if segment.kind == "rank":
            names.append(f"seg-{i + 1} (rank {segment.rank})")
        for stat in (getattr(segment, "stat", None), *getattr(segment, "stats", [])):
            if stat is not None and not stat.source:
                names.append(stat.label or stat.value)
        names.extend(it.name for it in getattr(segment, "items", []) if not it.source)
    if not names:
        return []
    return [Finding("unsourced", "warning", "no source for: " + ", ".join(names),
                    "add `source` to each item or stat, or one to its segment, so the claims can be checked")]


def _rule_ending(inputs: MapReelInputs) -> list[Finding]:
    last = inputs.segments[-1]
    if CTA.search(_text(last)) or (last.kind == "rank" and last.rank == 1):
        return []
    return [Finding("no-ending", "warning", "the last line ends without asking anything of the viewer",
                    "end on a question or a call to comment, like 'Which one did I miss?'", f"seg-{len(inputs.segments)}")]


def check(inputs: MapReelInputs, windows: Sequence[ShotWindow], *,
          names: Mapping[str, Sequence[str]] | None = None) -> list[Finding]:
    hard = "error" if inputs.mode == "build" and inputs.lint == "strict" else "warning"
    found: list[Finding] = []
    first = _text(inputs.segments[0])
    head = " ".join(first.split()[:HOOK_WINDOW_WORDS])
    if WEAK_START.search(first) or not HOOK_SIGNAL.search(head):
        found.append(Finding(
            "weak-opener", hard, f"the opening line has no hook: {head!r}",
            "open on a surprise, a number, a challenge or 'you' in the first seven words, never 'X has...'",
            "seg-1"))
    if TEASE.search(first):
        later = inputs.segments[1:]
        paid = any((s.kind == "rank" and s.rank == 1) or PAYOFF.search(_text(s)) for s in later)
        if not paid:
            found.append(Finding(
                "unpaid-loop", hard, "the opening teases a payoff that never comes",
                "end with a rank 1 segment, or say 'and number one is...' in a later line", "seg-1"))
    found.extend(_rule_beat_gap(windows, estimate_beats(inputs, windows, names=names)))
    found.extend(_rule_rehook(inputs, windows))
    found.extend(_rule_monotony(inputs))
    found.extend(_rule_length(inputs, windows))
    found.extend(_rule_unsourced(inputs))
    found.extend(_rule_ending(inputs))
    return found
