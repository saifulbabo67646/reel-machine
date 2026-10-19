"""The techreel lint: the errors that break narration, checked before a script is built."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from reelmachine.recipes.doodle.narration import provider_for

from .markup import units
from .models import Cue, TechReelInputs
from .schedule import word_index


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


def check(inputs: TechReelInputs, bound_provider: str | None = None) -> list[Finding]:
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
        for v, visual in enumerate(beat.visuals):
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
    return found
