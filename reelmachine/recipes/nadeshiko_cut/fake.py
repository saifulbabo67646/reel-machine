"""A deterministic Nadeshiko-shaped corpus for tests and dry runs.

It fabricates segments over the mock provider's synthetic audio, so any queried word
matches. Deployments can register it (it is a normal entry point) to exercise the whole
recipe with no API key, no network and no quota.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings, get_settings
from ...models import Media, Page, SearchPage, TextJa, Token, Translation
from ...sources.mock import MockProvider

MEDIA_ID = "MOCKMEDIA001"


class FakeNadeshikoCorpus:
    name = "nadeshiko-fake"

    def __init__(self, settings: Settings | None = None, *, count: int = 3):
        self.settings = settings or get_settings()
        self.count = count
        self.provider = MockProvider(self.settings)
        self.media = Media(
            publicId=MEDIA_ID,
            slug="mock-show",
            nameEn="Mock Show",
            category="ANIME",
        )
        self.last_filters: list[dict[str, Any] | None] = []

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "corpora",
            "description": "Deterministic fabricated segments over synthetic audio",
            "missing": [],
        }

    def _segments(self, word: str) -> list[Any]:
        segments = self.provider.make_segments(MEDIA_ID, 1, count=self.count)
        for segment in segments:
            segment.textJa = TextJa(
                content=f"{word}の話",
                highlight=f"<em>{word}</em>",
                tokens=[
                    Token(s=word, b=0, e=len(word), r=word),
                    Token(s="の", b=len(word), e=len(word) + 1, r="の"),
                    Token(s="話", b=len(word) + 1, e=len(word) + 2, d="話", r="はなし"),
                ],
            )
            segment.textEn = Translation(content="a line from the mock corpus")
        return segments

    def search(self, query: str | None = None, **kwargs: Any) -> SearchPage:
        self.last_filters.append(kwargs.get("filters"))
        segments = self._segments(str(query or ""))
        return SearchPage(
            segments=segments,
            pagination=Page(),
            media={self.media.publicId: self.media},
        )

    def iter_search(self, query: str | None = None, **kwargs: Any):
        yield from self._segments(str(query or ""))

    def segment_context(self, segment_id: str, **kwargs: Any) -> list[Any]:
        return []

    def download(self, url: str, dest, **kwargs: Any):  # pragma: no cover - unused
        raise NotImplementedError("the fake corpus serves no audio URLs")

    def close(self) -> None:
        return None

    def __enter__(self) -> "FakeNadeshikoCorpus":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
