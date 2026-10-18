"""The dialoguereel lint: checks a script before it is built."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .keys import match_keys
from .levels import jlpt_level, n_level
from .models import DialogueReelInputs
from .ruby import parse_ruby, plain_text
from .schedule import estimate

MIN_LINES = 3
MAX_LINES = 12
MIN_MS, MAX_MS = 15_000, 45_000
CJK_CHARS, OTHER_CHARS = 22, 42
MIN_KEYS, MAX_KEYS = 2, 6
MAX_RUN = 3


@dataclass
class Finding:
    rule: str
    severity: Literal["error", "warning"]
    message: str
    hint: str
    line: int | None = None
    key: int | None = None


def _non_latin(text: str) -> bool:
    return any(ch.isalpha() and ord(ch) > 0x024F for ch in text)


def _cjk(text: str) -> bool:
    return any("\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff"
               or "\uac00" <= ch <= "\ud7af" or "\u1100" <= ch <= "\u11ff" for ch in text)


def check(inputs: DialogueReelInputs, speakers: set[str]) -> list[Finding]:
    found: list[Finding] = []
    if inputs.mode != "cast" and not inputs.title.strip():
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
    if inputs.mode != "cast":
        found += _script_rules(inputs)
    return found


def _script_rules(inputs: DialogueReelInputs) -> list[Finding]:
    found: list[Finding] = []
    lines = inputs.lines
    ceiling = n_level(inputs.level) if inputs.language.startswith("ja") else None
    for i, line in enumerate(lines):
        plain = plain_text(parse_ruby(line.text)[0])
        if _non_latin(plain) and not (line.reading or "").strip():
            found.append(Finding("no-reading", "warning", f"line {i} has no reading",
                                 "add `reading` (romaji or pinyin) so learners who cannot read the script can follow", i))
        limit = CJK_CHARS if _cjk(plain) else OTHER_CHARS
        if len(plain) > limit:
            found.append(Finding("long-line", "warning", f"line {i} is {len(plain)} characters; keep it to {limit}",
                                 "split it into two lines; long lines are hard to read and speak in a short reel", i))
        spans = match_keys(line.text, line.keys)
        for j, (key, span) in enumerate(zip(line.keys, spans)):
            if span is None:
                found.append(Finding("key-missing", "warning", f"line {i}: key {key.text!r} is not in the line",
                                     "use the word as it appears in the line, or drop the key", i, j))
            level = jlpt_level(plain_text(parse_ruby(key.text)[0]), key.reading) if ceiling is not None else None
            if level is not None and level < ceiling:
                found.append(Finding("level", "warning", f"line {i}: key {key.text!r} is N{level}, harder than N{ceiling}",
                                     f"pick an N{ceiling} or easier word, or raise the reel's `level`", i, j))
    run = 1
    for i in range(1, len(lines)):
        run = run + 1 if lines[i].speaker == lines[i - 1].speaker else 1
        if run == MAX_RUN + 1:
            found.append(Finding("same-speaker", "warning",
                                 f"{lines[i].speaker!r} speaks {MAX_RUN + 1} lines in a row, ending at line {i}",
                                 "alternate speakers so it reads as a conversation", i))
    count = sum(len(line.keys) for line in lines)
    if not MIN_KEYS <= count <= MAX_KEYS:
        found.append(Finding("key-count", "warning", f"{count} key words; aim for {MIN_KEYS} to {MAX_KEYS}",
                             "the recap works best with a few words worth learning"))
    if lines:
        ms = estimate(inputs.model_copy(update={"flow": "standard"})).duration_ms
        if not MIN_MS <= ms <= MAX_MS:
            found.append(Finding("length", "warning", f"the reel runs about {ms / 1000:.0f} s; aim for 15 to 45 s",
                                 "add lines to lengthen it, or trim lines to shorten it"))
    quiz = inputs.quiz
    if quiz is not True and quiz is not False and not 0 <= quiz < len(lines):
        found.append(Finding("quiz-line", "error", f"quiz line {quiz} is outside the {len(lines)} lines",
                             f"use a line from 0 to {len(lines) - 1}, true for automatic, or false for none"))
    return found
