"""The dialoguereel lint: checks a script before it is built."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import DialogueReelInputs
from .ruby import parse_ruby

MIN_LINES = 3
MAX_LINES = 12


@dataclass
class Finding:
    rule: str
    severity: Literal["error", "warning"]
    message: str
    hint: str
    line: int | None = None


def check(inputs: DialogueReelInputs, speakers: set[str]) -> list[Finding]:
    found: list[Finding] = []
    if not inputs.title.strip():
        found.append(Finding("no-title", "error", "the reel has no title",
                             "set `title`; it is the hook text shown while the first line starts"))
    for i, line in enumerate(inputs.lines):
        if line.speaker not in speakers:
            found.append(Finding("unknown-speaker", "error",
                                 f"line {i} is spoken by {line.speaker!r}, who is not in the cast",
                                 f"use one of: {', '.join(sorted(speakers))}", i))
    if inputs.mode != "cast" and not MIN_LINES <= len(inputs.lines) <= MAX_LINES:
        found.append(Finding("line-count", "error",
                             f"{len(inputs.lines)} lines; a dialogue needs {MIN_LINES} to {MAX_LINES}",
                             "a reel under 3 lines is too thin to teach anything; over 12 runs too long"))
    for i, line in enumerate(inputs.lines):
        for problem in parse_ruby(line.text)[1]:
            found.append(Finding("ruby", "warning", f"line {i}: {problem}",
                                 "furigana goes in braces right after the kanji: 手伝{てつだ}い", i))
    return found
