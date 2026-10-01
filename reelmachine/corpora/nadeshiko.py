"""Nadeshiko as a corpus provider: search, dialogue context, reference audio.

Thin explicit delegation rather than `__getattr__`, so the interface a recipe depends on
is visible in one place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings
from ..nadeshiko import NadeshikoClient


class NadeshikoCorpus:
    name = "nadeshiko"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings
        self.client = NadeshikoClient(settings=settings)

    def missing(self) -> list[str]:
        settings = self.settings
        if settings is None:
            from ..config import get_settings

            settings = get_settings()
        if not settings.api_key:
            return ["NADESHIKO_API_KEY is not set"]
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "corpora",
            "description": "Nadeshiko search, segment context and reference audio",
            "missing": self.missing(),
        }

    # -- the corpus surface the nadeshiko-cut recipe uses ----------------------
    def search(self, *args: Any, **kwargs: Any) -> Any:
        return self.client.search(*args, **kwargs)

    def iter_search(self, *args: Any, **kwargs: Any) -> Any:
        return self.client.iter_search(*args, **kwargs)

    def segment_context(self, *args: Any, **kwargs: Any) -> Any:
        return self.client.segment_context(*args, **kwargs)

    def download(self, url: str, dest: Path, **kwargs: Any) -> Path:
        return self.client.download(url, dest, **kwargs)

    def quota(self) -> Any:
        return self.client.quota()

    # -- lifecycle -------------------------------------------------------------
    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "NadeshikoCorpus":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
