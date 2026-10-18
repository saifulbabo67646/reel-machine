"""Ruby markup for furigana: `手伝{てつだ}いましょうか。`.

A reading in braces attaches to the kanji run just before it. Anything else that
contains a brace stays literal text and is reported as a problem (the caller turns
problems into lint warnings), so a typo never silently eats characters.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

KANJI = "㐀-䶿一-鿿々〆ヶ"
_RUBY = re.compile(rf"([{KANJI}]+)\{{([^{{}}]+)\}}")


@dataclass(frozen=True)
class Run:
    text: str
    ruby: str | None = None


def parse_ruby(text: str) -> tuple[list[Run], list[str]]:
    """Split `text` into runs; returns (runs, problems). Adjacent plain text is one run."""
    runs: list[Run] = []
    problems: list[str] = []
    literal = ""
    literal_start = 0

    def flush() -> None:
        nonlocal literal
        if literal:
            runs.append(Run(literal, None))
            for m in re.finditer(r"[{}]", literal):
                problems.append(f"braces at {literal_start + m.start()} are not after a kanji run")
            literal = ""

    pos = 0
    for m in _RUBY.finditer(text):
        # The kanji run may start inside text already scanned as literal; the regex
        # is greedy from the leftmost kanji, so m.start() >= pos always holds.
        if not literal:
            literal_start = pos
        literal += text[pos:m.start()]
        flush()
        runs.append(Run(m.group(1), m.group(2)))
        pos = m.end()
    if not literal:
        literal_start = pos
    literal += text[pos:]
    flush()
    return runs, problems


def plain_text(runs: Sequence[Run]) -> str:
    """What the voice reads: the runs' text joined, no readings."""
    return "".join(r.text for r in runs)
