"""Where the subtitle script comes from.

A `SubtitleProvider` turns (tmdb id / language / the downloaded film) into a subtitle
file we can parse. Four implementations ship: `embedded` (extract from the film's own
streams — keyless and perfectly synced), `subdl` and `opensubtitles` (the two
TMDB-native databases), and `auto` (the chain, so the caller configures nothing).
`fake` is the deterministic test double.

The provider owns *where* the file came from and its licence; the stage owns
normalising it to UTF-8 SRT and making its timing agree with our copy of the film.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ....core.assets import Licence

#: The order `auto` walks unless REEL_SUBTITLES_ORDER says otherwise. Embedded first:
#: it costs nothing and its timestamps came out of the same file we will cut.
DEFAULT_ORDER = ("embedded", "subdl", "opensubtitles")

_LANGUAGES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "ur": "ur",
    "urd": "ur",
    "urdu": "ur",
    "hi": "hi",
    "hin": "hi",
    "hindi": "hi",
    "hinglish": "hi",
    "ar": "ar",
    "ara": "ar",
    "arabic": "ar",
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "ja": "ja",
    "jpn": "ja",
    "jp": "ja",
    "japanese": "ja",
    "ko": "ko",
    "kor": "ko",
    "korean": "ko",
    "zh": "zh",
    "chi": "zh",
    "zho": "zh",
    "chinese": "zh",
    "fr": "fr",
    "fre": "fr",
    "fra": "fr",
    "french": "fr",
    "de": "de",
    "ger": "de",
    "deu": "de",
    "german": "de",
    "pt": "pt",
    "por": "pt",
    "portuguese": "pt",
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
    "tr": "tr",
    "tur": "tr",
    "turkish": "tr",
}


def normalise_language(value: str | None) -> str:
    """`English`, `eng`, `EN` → `en`; unknown tags keep their first two letters."""
    text = (value or "").strip().lower()
    if not text:
        return ""
    if text in _LANGUAGES:
        return _LANGUAGES[text]
    match = "".join(ch for ch in text if ch.isalpha())[:3]
    if len(match) == 3 and match in _LANGUAGES:
        return _LANGUAGES[match]
    return text[:2]


def language_rank(tag: str, wanted: list[str]) -> int:
    """0 for the first wanted language, 1 for the next, … — `99` when not wanted."""
    normalised = normalise_language(tag)
    for index, language in enumerate(wanted):
        if normalised == normalise_language(language):
            return index
    return 99 if wanted else 0


def chain_order() -> tuple[str, ...]:
    raw = os.environ.get("REEL_SUBTITLES_ORDER", "")
    names = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return names or DEFAULT_ORDER


class SubtitleRequest(BaseModel):
    """What a provider needs to find the right file."""

    model_config = ConfigDict(extra="forbid")

    tmdb_id: int = 0
    media_type: str = "movie"
    season: int = 1
    episode: int = 1
    title: str = ""
    year: int | None = None
    imdb_id: str = ""
    languages: list[str] = Field(default_factory=list)  # preference order, normalised
    movie_path: str = ""
    duration_ms: int = 0

    def primary_language(self) -> str:
        return self.languages[0] if self.languages else "en"


class SubtitleFetch(BaseModel):
    """One usable subtitle file."""

    model_config = ConfigDict(extra="forbid")

    path: str
    language: str = ""
    source: str = ""
    licence: Licence | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class SubtitleProvider(Protocol):
    name: str

    def missing(self) -> list[str]: ...

    def describe(self) -> dict[str, Any]: ...

    def fetch(self, request: SubtitleRequest, *, dest_dir: Path) -> SubtitleFetch | None: ...


def describe_row(name: str, description: str, missing: list[str], **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name,
        "group": "subtitles",
        "description": description,
        "missing": list(missing),
    }
    row.update(extra)
    return row
