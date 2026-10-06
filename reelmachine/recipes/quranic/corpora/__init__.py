"""Quran corpus providers.

The protocol lives with the recipe that needs it (ADR-0011): two implementations serve
text, translations, recitation audio and word timings, and a deterministic fake drives
the whole recipe offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import QuranSelection


@runtime_checkable
class QuranCorpus(Protocol):
    name: str

    def fetch(self, inputs) -> QuranSelection: ...

    def fetch_audio(self, url: str, dest: Path) -> Path: ...

    def missing(self) -> list[str]: ...

    def describe(self) -> dict: ...
