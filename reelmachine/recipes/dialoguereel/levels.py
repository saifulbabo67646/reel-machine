"""JLPT levels from the bundled vocabulary (`reelmachine/data/jlpt_vocab.json`)."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "data" / "jlpt_vocab.json"


@cache
def _index() -> dict[str, int]:
    """Word or reading -> easiest level (highest number) it appears at."""
    out: dict[str, int] = {}
    for word, reading, level in json.loads(_PATH.read_text(encoding="utf-8")):
        for form in (word, reading):
            if form and level > out.get(form, 0):
                out[form] = level
    return out


def jlpt_level(text: str, reading: str | None = None) -> int | None:
    """5..1 (5 = N5) for `text`, else for `reading`; None when neither is listed."""
    index = _index()
    for form in (text, reading):
        if form and form in index:
            return index[form]
    return None


def n_level(label: str) -> int | None:
    """"N5"/"n3" -> 5/3; anything else -> None."""
    m = re.fullmatch(r"[Nn]([1-5])", label.strip())
    return int(m.group(1)) if m else None


def primary_language(language: str) -> str:
    """"ja-JP"/"JA"/"ja_jp" -> "ja": the lower-cased primary subtag of a language tag."""
    return language.strip().lower().replace("_", "-").split("-")[0]


def is_japanese(language: str) -> bool:
    """Whether JLPT levels apply: the reel's language is Japanese, however its tag is written."""
    return primary_language(language) == "ja"
