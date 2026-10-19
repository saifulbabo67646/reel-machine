"""The techreel lint: the errors that break narration, checked before a script is built."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from reelmachine.recipes.doodle.narration import provider_for

from ...core.errors import InvalidInput
from . import assets
from .markup import plain, units
from .models import Cue, TechReelInputs
from .schedule import cue_ms, estimate, exit_ms, word_index
from .words import unit_times


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Literal["error", "warning"]
    message: str
    hint: str
    path: str


def blocks(inputs: TechReelInputs) -> bool:
    """Whether errors stop the build (a storyboard always goes through)."""
    return inputs.mode == "full" and inputs.lint == "strict"


def _cues(visual) -> list[tuple[str, Cue]]:
    """Every cue a visual carries, with its path relative to the visual."""
    found: list[tuple[str, Cue]] = []
    if visual.at is not None:
        found.append(("at", visual.at))
    if isinstance(visual.exit, Cue):
        found.append(("exit", visual.exit))
    if getattr(visual, "click_at", None) is not None:
        found.append(("click_at", visual.click_at))
    for k, move in enumerate(getattr(visual, "cursor", [])):
        found.append((f"cursor[{k}].at", move.at))
    return found


HOOK_MAX_MS = 3500          # the first line must land before a thumb moves
STATIC_MAX_MS = 3500        # a stretch of one beat with nothing new on screen
BEAT_MAX_MS = 9000
BEAT_MAX_LINES = 4
LIVE_MAX = 4                # non-hero visuals on screen at once
STAMP_MAX = 16              # characters: a longer stamp shrinks to fit the stage and stops reading as a stamp
LENGTH_MS = (30_000, 150_000)
TEXT_RATIO = (0.4, 2.5)
_DEVANAGARI = range(0x0900, 0x0980)


def _timing(inputs: TechReelInputs):
    """Per beat: its estimated slot, and each visual's estimated (enter, exit) ms."""
    plan = estimate(inputs)
    out = []
    for b, beat in enumerate(inputs.beats):
        slot = plan.beats[b]
        us = [units(ln.caption, inputs.language) for ln in beat.lines]
        times = [unit_times([], u, ln.speech_ms) for u, ln in zip(us, slot.lines, strict=True)]
        nxt = plan.beats[b + 1].start_ms if b + 1 < len(plan.beats) else None
        spans = []
        for v in beat.visuals:
            enter = cue_ms(v.at, slot, times, us)[0]
            spans.append((enter, max(enter, exit_ms(v.exit, slot, nxt, times, us))))
        out.append((slot, spans))
    return plan, out


def _bare_cta(beat) -> bool:
    """A call to action with no stage visuals of its own (hero moves do not count): compose draws the standard ending."""
    return beat.kind == "cta" and all(v.kind == "hero" for v in beat.visuals)


def _pacing(inputs: TechReelInputs, found: list[Finding]) -> None:
    plan, timing = _timing(inputs)
    for b, (beat, (slot, spans)) in enumerate(zip(inputs.beats, timing, strict=True)):
        if b == 0:
            first = slot.lines[0].speech_ms
            if first > HOOK_MAX_MS or not beat.visuals:
                found.append(Finding("hook-slow", "warning",
                                     "the hook has no visual" if not beat.visuals
                                     else f"the first line runs about {first / 1000:.1f} s",
                                     f"open on a visual and keep the first line under {HOOK_MAX_MS / 1000:.1f} s",
                                     "beats[0]"))
        if len(beat.lines) > BEAT_MAX_LINES or slot.end_ms - slot.start_ms > BEAT_MAX_MS:
            found.append(Finding("beat-long", "warning",
                                 f"beat {b} has {len(beat.lines)} lines, about {(slot.end_ms - slot.start_ms) / 1000:.1f} s",
                                 f"split it: at most {BEAT_MAX_LINES} lines and {BEAT_MAX_MS // 1000} s", f"beats[{b}]"))
        marks = {slot.start_ms, slot.end_ms, *(min(e, slot.end_ms) for e, _ in spans)}
        if _bare_cta(beat):   # compose adds the comment box at line 0 and the follow card at line 1
            marks |= {ln.start_ms for ln in slot.lines[:2]}
        marks = sorted(marks)
        if any(y - x > STATIC_MAX_MS for x, y in pairwise(marks)):
            found.append(Finding("static", "warning",
                                 f"beat {b} shows nothing new for over {STATIC_MAX_MS / 1000:.1f} s",
                                 "cue another visual into the stretch, or shorten the lines", f"beats[{b}]"))
        events = sorted([(e, 1) for (e, _), v in zip(spans, beat.visuals, strict=True) if v.kind != "hero"]
                        + [(x, -1) for (_, x), v in zip(spans, beat.visuals, strict=True) if v.kind != "hero"],
                        key=lambda p: (p[0], p[1]))        # a leaving visual frees its place before one enters
        live = peak = 0
        for _, d in events:
            live += d
            peak = max(peak, live)
        if peak > LIVE_MAX:
            found.append(Finding("crowded", "warning", f"beat {b} has {peak} visuals on screen at once",
                                 f"keep it to {LIVE_MAX}: give some an earlier `exit`", f"beats[{b}].visuals"))
    if inputs.beats and not LENGTH_MS[0] <= plan.duration_ms <= LENGTH_MS[1]:
        found.append(Finding("length", "warning", f"the reel runs about {plan.duration_ms / 1000:.0f} s",
                             f"aim for {LENGTH_MS[0] // 1000} to {LENGTH_MS[1] // 1000} s", "beats"))


def _structure(inputs: TechReelInputs, found: list[Finding]) -> None:
    beats = inputs.beats
    if not beats:
        return
    if beats[0].kind != "hook":
        found.append(Finding("structure", "warning", "the first beat is not a hook",
                             'set kind "hook" on the opening beat', "beats[0].kind"))
    last = len(beats) - 1
    if beats[-1].kind != "cta":
        found.append(Finding("structure", "warning", "the last beat is not a call to action",
                             'end on a beat of kind "cta"', f"beats[{last}].kind"))
    elif _bare_cta(beats[-1]) and inputs.creator is None:
        found.append(Finding("cta-no-creator", "warning", "the ending has no follow card: set creator",
                             "give `creator` a name and handle, or cue a follow visual", "creator"))
    if not any(b.kind == "honest" for b in beats):
        found.append(Finding("structure", "warning", "no honest beat (the catch or the limit)",
                             'add a beat of kind "honest": trust is what makes people follow', "beats"))


def _assets(inputs: TechReelInputs, allow_local: bool, found: list[Finding]) -> None:
    try:
        assets.check_svgs(inputs)
    except InvalidInput as exc:
        path = exc.details.get("path", "")
        found.append(Finding("svg", "error", f"{path}: {exc.message}", exc.hint or "fix the drawing", path))
    for path, src in assets.image_sources(inputs):
        if assets.is_local(src):
            if not allow_local:
                found.append(Finding("image-local", "error", f"{path} is a local path",
                                     "local files are read for local runs only: use an https URL", path))
        elif not assets.is_url(src):     # ftp:, file:, data: ... the assets stage refuses them
            found.append(Finding("image-scheme", "error", f"{path} is neither an https URL nor a local path",
                                 "use an https URL (or, on a local run, a file path)", path))


def check(inputs: TechReelInputs, bound_provider: str | None = None, *, allow_local: bool = True) -> list[Finding]:
    found: list[Finding] = []
    if not inputs.beats:
        found.append(Finding("no-beats", "error", "the reel has no beats",
                             "add at least one beat with a line to say", "beats"))
    if bound_provider is not None and provider_for(inputs.voice) != bound_provider:
        found.append(Finding("voice-provider", "error",
                             f"voice {inputs.voice!r} is not from the bound provider {bound_provider!r}",
                             f"use a {bound_provider} voice, or set it as `{bound_provider}:<voice>`", "voice"))
    for b, beat in enumerate(inputs.beats):
        for n, line in enumerate(beat.lines):
            path = f"beats[{b}].lines[{n}].say"
            if not line.say.strip():
                found.append(Finding("empty-say", "error", f"{path} is empty",
                                     "give the line words to speak", path))
                continue
            if any(ch.isdigit() for ch in line.say):
                found.append(Finding("digit-in-say", "error", f"{path} contains a digit",
                                     'write numbers and names with digits as words in the voice\'s language, '
                                     'e.g. "GPT-4o" → "G P T four o"; keep the digits in "text"', path))
            if "*" in line.say:
                found.append(Finding("markup-in-say", "error", f"{path} contains `*`",
                                     "accent markup belongs in `text`; the voice would read it aloud", path))
            if line.text and line.say.strip():
                ratio = len(plain(line.text)) / len(line.say.strip())
                if not TEXT_RATIO[0] <= ratio <= TEXT_RATIO[1]:
                    tpath = f"beats[{b}].lines[{n}].text"
                    found.append(Finding("text-mismatch", "warning",
                                         f"{tpath} is {ratio:.1f}x as long as what is said",
                                         "the caption should be the spoken words, written for the eye", tpath))
            letters = [ch for ch in line.say if ch.isalpha()]
            if (inputs.language.split("-")[0].lower() == "hi" and letters
                    and sum(ord(ch) in _DEVANAGARI for ch in letters) / len(letters) < 0.5):
                found.append(Finding("hindi-latin", "warning", f"{path} is mostly Latin letters",
                                     "a Hindi voice reads Devanagari: write `say` in Devanagari, the Latin goes in `text`",
                                     path))
        for v, visual in enumerate(beat.visuals):
            if visual.kind == "stamp" and len(visual.text.strip()) > STAMP_MAX:
                spath = f"beats[{b}].visuals[{v}].text"
                found.append(Finding("stamp-long", "warning",
                                     f"{spath} is {len(visual.text.strip())} characters",
                                     f"keep a stamp to one or two short words (at most {STAMP_MAX} characters)",
                                     spath))
            if visual.kind == "window" and not 0 <= visual.active < max(len(visual.tabs), 1):
                apath = f"beats[{b}].visuals[{v}].active"
                found.append(Finding("active-tab", "warning", f"{apath} is {visual.active}, not one of the tabs",
                                     f"use 0 to {max(len(visual.tabs), 1) - 1}", apath))
            for where, cue in _cues(visual):
                path = f"beats[{b}].visuals[{v}].{where}.line"
                if cue.line >= len(beat.lines):
                    found.append(Finding("cue-line", "error",
                                         f"{path} is {cue.line}, but the beat has {len(beat.lines)} lines",
                                         f"use a line from 0 to {len(beat.lines) - 1}", path))
                elif cue.word is not None:
                    caption = units(beat.lines[cue.line].caption, inputs.language)
                    if word_index(cue, caption) is None:
                        wpath = f"beats[{b}].visuals[{v}].{where}.word"
                        found.append(Finding("cue-word", "warning",
                                             f"{wpath} {cue.word!r} is not a word of line {cue.line}",
                                             "the cue falls back to the start of the line; "
                                             "use a word as it appears in the caption", wpath))
    _assets(inputs, allow_local, found)
    if inputs.beats:
        _pacing(inputs, found)
    _structure(inputs, found)
    if not inputs.sources:
        found.append(Finding("no-sources", "warning", "the reel lists no sources",
                             "put the URL of every fact's primary source in `sources`", "sources"))
    return found
