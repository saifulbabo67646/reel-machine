"""Pydantic mirrors of the Nadeshiko OpenAPI v2.4.19 schemas.

Only the fields reel-machine actually consumes are typed explicitly; every model
allows extra keys so a server-side addition never breaks a run.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ContentRating = Literal["SAFE", "SUGGESTIVE", "QUESTIONABLE", "EXPLICIT"]
Category = Literal["ANIME", "JDRAMA", "YOUTUBE"]
SegmentStatus = Literal["ACTIVE", "HIDDEN", "DELETED"]
SortMode = Literal["RELEVANCE", "ASC", "DESC", "TIME_ASC", "TIME_DESC", "RANDOM"]


class Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Token(Base):
    """Morphological token (`s`urface, `d`ictionary, `r`eading, `b`egin, `e`nd, `p`os)."""

    s: str
    d: str | None = None
    r: str | None = None
    b: int
    e: int
    p: str | None = None
    posLabel: str | None = None
    pt: str | None = None
    kind: str | None = None


class TextJa(Base):
    content: str
    highlight: str | None = None
    tokens: list[Token] | None = None


class Translation(Base):
    content: str
    isMachineTranslated: bool = False
    highlight: str | None = None


class SegmentUrls(Base):
    imageUrl: str
    audioUrl: str
    videoUrl: str


class Segment(Base):
    """One dialogue segment: an exact, verifiable window into an episode."""

    publicId: str
    position: int
    status: SegmentStatus = "ACTIVE"
    startTimeMs: int
    endTimeMs: int
    contentRating: ContentRating = "SAFE"
    episode: int
    externalVideoId: str | None = None
    mediaPublicId: str
    textJa: TextJa
    textEn: Translation
    textEs: Translation
    urls: SegmentUrls

    @property
    def duration_ms(self) -> int:
        return max(0, self.endTimeMs - self.startTimeMs)

    @property
    def japanese(self) -> str:
        return self.textJa.content.strip()

    @property
    def english(self) -> str:
        return self.textEn.content.strip()

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.publicId} ep{self.episode} {self.startTimeMs}-{self.endTimeMs}ms {self.japanese}"


class ExternalId(Base):
    anilist: str | None = None
    imdb: str | None = None
    tvdb: str | None = None
    tmdb: str | None = None
    youtube: str | None = None


class Media(Base):
    publicId: str
    slug: str
    externalIds: ExternalId = Field(default_factory=ExternalId)
    nameJa: str = ""
    nameRomaji: str = ""
    nameEn: str = ""
    airingFormat: str | None = None
    airingStatus: str | None = None
    genres: list[str] = Field(default_factory=list)
    coverUrl: str | None = None
    bannerUrl: str | None = None
    startDate: str | None = None
    endDate: str | None = None
    category: Category = "ANIME"
    segmentCount: int = 0
    episodeCount: int = 0
    studio: str | None = None
    seasonName: str | None = None
    seasonYear: int | None = None

    @property
    def names(self) -> list[str]:
        return [n for n in (self.nameEn, self.nameRomaji, self.nameJa) if n]


class MediaSummary(Base):
    publicId: str
    slug: str
    nameJa: str = ""
    nameRomaji: str = ""
    nameEn: str = ""
    coverUrl: str | None = None
    category: Category = "ANIME"


class Episode(Base):
    mediaPublicId: str
    episodeNumber: int
    titleEn: str | None = None
    titleRomaji: str | None = None
    titleJa: str | None = None
    description: str | None = None
    airedAt: str | None = None
    lengthSeconds: int | None = None
    thumbnailUrl: str | None = None
    externalVideoId: str | None = None
    segmentCount: int = 0


class WordMatchMedia(Base):
    mediaPublicId: str
    matchCount: int


class WordMatch(Base):
    word: str
    isMatch: bool
    matchCount: int
    realMatchCount: int
    media: list[WordMatchMedia] = Field(default_factory=list)


class Page(Base):
    hasMore: bool = False
    cursor: str | None = None
    estimatedTotalHits: int = 0
    estimatedTotalHitsRelation: str | None = None


class SearchPage(Base):
    """A page of `/v1/search` plus the optional `include[]=media` expansion."""

    segments: list[Segment] = Field(default_factory=list)
    pagination: Page = Field(default_factory=Page)
    media: dict[str, Media] = Field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "SearchPage":
        includes = payload.get("includes") or {}
        return cls(
            segments=[Segment.model_validate(s) for s in payload.get("segments") or []],
            pagination=Page.model_validate(payload.get("pagination") or {}),
            media={
                pid: Media.model_validate(m)
                for pid, m in (includes.get("media") or {}).items()
            },
        )
