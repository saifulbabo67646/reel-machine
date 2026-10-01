"""Input, plan and stage payload models for the nadeshiko-cut recipe.

The stage payloads are the serializable contract between stages: each stage reads the
previous stage's JSON and writes its own, which is what makes a job resumable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...align import TimelineMap
from ...matching import describe as describe_match
from ...models import Media, Segment, Token
from ...sources import EpisodeAsset
from ...core.timeline import Timeline

_CORPUS_ALIASES = {"anime": "ANIME", "jdrama": "JDRAMA", "drama": "JDRAMA", "youtube": "YOUTUBE"}
_KNOWN_CORPORA = {"ANIME", "JDRAMA", "YOUTUBE"}


def parse_corpus(corpus: str) -> list[str]:
    """`"anime+jdrama"` → `["ANIME", "JDRAMA"]`; the one corpus parameter.

    Accepts `+`, `,`, `;` or whitespace separators and the `drama` alias, so the CLI's
    `--category anime,jdrama` and the API's `corpus` field are the same thing.
    """
    out: list[str] = []
    for chunk in re.split(r"[+,;\s]+", (corpus or "").strip()):
        if not chunk:
            continue
        key = _CORPUS_ALIASES.get(chunk.lower(), chunk.upper())
        if key not in _KNOWN_CORPORA:
            raise ValueError(f"unknown corpus {chunk!r}; expected anime, jdrama or youtube")
        if key not in out:
            out.append(key)
    return out or ["ANIME", "JDRAMA"]


class NadeshikoCutInputs(BaseModel):
    """Everything a caller can ask of this recipe."""

    model_config = ConfigDict(extra="forbid")

    word: str
    mode: Literal["plan", "build"] = "build"
    corpus: str | None = None  # "anime", "jdrama", "anime+jdrama", "youtube", …; None = setting
    count: int | None = None
    per_media: int = 1
    per_category: int | None = None
    content_rating: list[str] | None = None
    exact_match: bool = False
    only: list[str] = Field(default_factory=list)  # "<mediaPublicId>:<episode>"

    aspect: Literal["vertical", "square", "original"] | None = None
    pre_roll_ms: int | None = None
    post_roll_ms: int | None = None
    watermark: str = ""
    name: str | None = None
    outdir: str | None = None

    dry_run: bool = False
    plan_out: str | None = None

    def categories(self) -> list[str] | None:
        """The corpus parameter as a filter list; None leaves `REEL_CATEGORY` in charge."""
        return parse_corpus(self.corpus) if self.corpus else None

    def only_pairs(self) -> list[tuple[str, int]]:
        pairs: list[tuple[str, int]] = []
        for entry in self.only:
            media_id, _, episode = entry.partition(":")
            if not media_id or not episode.strip().isdigit():
                raise ValueError(f"bad --only entry {entry!r}; expected <mediaPublicId>:<episode>")
            pairs.append((media_id.strip(), int(episode)))
        return pairs


class StageOutput(BaseModel):
    """Base for stage payloads; `halt_pipeline` stops a plan-only run after compose."""

    model_config = ConfigDict(extra="forbid")

    halt_pipeline: bool = False


class AssetHandle(BaseModel):
    """A serializable `EpisodeAsset` (the ffmpeg-openable source of one episode)."""

    model_config = ConfigDict(extra="forbid")

    media_public_id: str
    episode: int
    url: str
    duration_ms: int = 0
    label: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    local_path: str | None = None
    audio_track: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_asset(cls, asset: EpisodeAsset) -> "AssetHandle":
        return cls(
            media_public_id=asset.media_public_id,
            episode=asset.episode,
            url=asset.url,
            duration_ms=asset.duration_ms,
            label=asset.label,
            headers=dict(asset.headers),
            local_path=str(asset.local_path) if asset.local_path else None,
            audio_track=asset.audio_track,
            meta=dict(asset.meta),
        )

    def to_asset(self) -> EpisodeAsset:
        return EpisodeAsset(
            media_public_id=self.media_public_id,
            episode=self.episode,
            url=self.url,
            duration_ms=self.duration_ms,
            label=self.label,
            headers=dict(self.headers),
            local_path=Path(self.local_path) if self.local_path else None,
            audio_track=self.audio_track,
            meta=dict(self.meta),
        )

    def source_id(self, provider: str) -> str:
        """Stable id for this resolved source, used by the timeline's elements."""
        import hashlib

        return hashlib.sha256(f"{provider}|{self.url}".encode("utf-8")).hexdigest()[:32]


# ------------------------------------------------------------------ functional API

@dataclass(slots=True)
class PlannedSegment:
    segment: Segment
    media: Media | None = None
    asset: EpisodeAsset | None = None
    timeline: TimelineMap | None = None
    local_start_ms: int = 0
    local_end_ms: int = 0
    scene_start_ms: int = 0   # source-time window, including the neighbouring lines
    scene_end_ms: int = 0
    context_count: int = 0
    word: str = ""            # the word this clip is meant to teach
    category: str = "ANIME"   # ANIME | JDRAMA — which corpus it came from
    status: str = "pending"  # ok | unaligned | unresolved | out-of-range
    note: str = ""

    @property
    def key(self) -> str:
        return self.segment.publicId

    @property
    def media_name(self) -> str:
        if self.media is None:
            return self.segment.mediaPublicId
        return self.media.nameEn or self.media.nameRomaji or self.media.nameJa

    @property
    def source_label(self) -> str:
        return f"{self.media_name} · ep {self.segment.episode}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "segmentId": self.segment.publicId,
            "mediaPublicId": self.segment.mediaPublicId,
            "media": self.media_name,
            "category": self.category,
            "episode": self.segment.episode,
            "srcStartMs": self.segment.startTimeMs,
            "srcEndMs": self.segment.endTimeMs,
            "sceneStartMs": self.scene_start_ms,
            "sceneEndMs": self.scene_end_ms,
            "contextSegments": self.context_count,
            "matchedForm": describe_match(self.segment, self.word),
            "localStartMs": self.local_start_ms,
            "localEndMs": self.local_end_ms,
            "status": self.status,
            "note": self.note,
            "textJa": self.segment.japanese,
            "textEn": self.segment.english,
            "contentRating": self.segment.contentRating,
            "timeline": self.timeline.to_dict() if self.timeline else None,
            "asset": self.asset.url if self.asset else None,
            "audioTrack": self.asset.audio_track if self.asset else None,
        }

    def to_record(self) -> "ItemRecord":
        """The stage-facing view of the same data (used by the functional API)."""
        record = ItemRecord(
            segment=self.segment,
            media=self.media,
            category=self.category,
            word=self.word,
            scene_start_ms=self.scene_start_ms,
            scene_end_ms=self.scene_end_ms,
            context_count=self.context_count,
            status=self.status,
            note=self.note,
            local_start_ms=self.local_start_ms,
            local_end_ms=self.local_end_ms,
            timeline=self.timeline.to_dict() if self.timeline else None,
        )
        if self.asset is not None:
            record.asset = AssetHandle.from_asset(self.asset)
        return record


@dataclass(slots=True)
class Plan:
    """The legacy planning view: what `plan.json` and `reel plan` show."""

    word: str
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = ""
    items: list[PlannedSegment] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok_items(self) -> list[PlannedSegment]:
        return [i for i in self.items if i.status == "ok"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "created": self.created,
            "source": self.source,
            "stats": self.stats,
            "items": [i.to_dict() for i in self.items],
        }

    def save(self, path: Path) -> Path:
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


# ------------------------------------------------------------------- stage payloads

class ItemRecord(BaseModel):
    """One selected candidate, carried through every stage."""

    model_config = ConfigDict(extra="forbid")

    segment: Segment
    media: Media | None = None
    category: str = "ANIME"
    word: str = ""
    scene_start_ms: int = 0       # window the alignment locks onto
    scene_end_ms: int = 0
    context_count: int = 0
    context: list[Segment] = Field(default_factory=list)
    status: str = "pending"
    note: str = ""
    asset: AssetHandle | None = None
    timeline: dict[str, Any] | None = None
    local_start_ms: int = 0
    local_end_ms: int = 0

    @property
    def key(self) -> str:
        return self.segment.publicId

    @property
    def media_name(self) -> str:
        if self.media is None:
            return self.segment.mediaPublicId
        return self.media.nameEn or self.media.nameRomaji or self.media.nameJa

    @property
    def source_label(self) -> str:
        return f"{self.media_name} · ep {self.segment.episode}"

    def to_planned(self) -> PlannedSegment:
        item = PlannedSegment(
            segment=self.segment,
            media=self.media,
            asset=self.asset.to_asset() if self.asset else None,
            local_start_ms=self.local_start_ms,
            local_end_ms=self.local_end_ms,
            scene_start_ms=self.scene_start_ms,
            scene_end_ms=self.scene_end_ms,
            context_count=self.context_count,
            word=self.word,
            category=self.category,
            status=self.status,
            note=self.note,
        )
        if self.timeline:
            item.timeline = timeline_from_dict(self.timeline)
        return item

    def to_dict(self) -> dict[str, Any]:
        """The plan/manifest view — identical to `PlannedSegment.to_dict()`."""
        return {
            "segmentId": self.segment.publicId,
            "mediaPublicId": self.segment.mediaPublicId,
            "media": self.media_name,
            "category": self.category,
            "episode": self.segment.episode,
            "srcStartMs": self.segment.startTimeMs,
            "srcEndMs": self.segment.endTimeMs,
            "sceneStartMs": self.scene_start_ms,
            "sceneEndMs": self.scene_end_ms,
            "contextSegments": self.context_count,
            "matchedForm": describe_match(self.segment, self.word),
            "localStartMs": self.local_start_ms,
            "localEndMs": self.local_end_ms,
            "status": self.status,
            "note": self.note,
            "textJa": self.segment.japanese,
            "textEn": self.segment.english,
            "contentRating": self.segment.contentRating,
            "timeline": self.timeline,
            "asset": self.asset.url if self.asset else None,
            "audioTrack": self.asset.audio_track if self.asset else None,
        }


class SelectOutput(StageOutput):
    inputs: NadeshikoCutInputs
    source: str = ""
    items: list[ItemRecord] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class EpisodeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_public_id: str
    episode: int
    label: str = ""
    asset: AssetHandle
    source_id: str = ""
    clip_paths: dict[str, str] = Field(default_factory=dict)


class AcquireOutput(StageOutput):
    inputs: NadeshikoCutInputs
    items: list[ItemRecord] = Field(default_factory=list)
    episodes: list[EpisodeRecord] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    failures: dict[str, str] = Field(default_factory=dict)


class ComposeOutput(StageOutput):
    inputs: NadeshikoCutInputs
    word: str = ""
    source: str = ""
    plan: dict[str, Any] = Field(default_factory=dict)
    items: list[ItemRecord] = Field(default_factory=list)
    episodes: list[EpisodeRecord] = Field(default_factory=list)
    timeline: Timeline
    render_options: dict[str, Any] = Field(default_factory=dict)


class CueData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int
    end_ms: int
    japanese: str
    english: str
    word: str = ""
    tokens: list[Token] | None = None
    marked: str | None = None
    source: str = ""
    index_label: str = ""
    reading: str = ""


class ClipRecord(BaseModel):
    """One materialised cut, with everything the render stage needs."""

    model_config = ConfigDict(extra="forbid")

    index: int                # 1-based position among the reel's clips
    item_index: int           # 1-based position among selected items (naming uses it)
    segment_id: str
    clip: str                 # path to the cut file
    clip_asset: str = ""      # asset id of the same bytes
    duration_ms: int = 0
    start_ms: int = 0         # position on the reel timeline
    cue: CueData
    manifest_item: dict[str, Any] = Field(default_factory=dict)


class PrepOutput(StageOutput):
    timeline: Timeline
    clips: list[ClipRecord] = Field(default_factory=list)
    duration_ms: int = 0
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    manifest_items: list[dict[str, Any]] = Field(default_factory=list)
    render_options: dict[str, Any] = Field(default_factory=dict)


class RenderOutput(StageOutput):
    video: str
    ass: str
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = "cut"
    timeline_digest: str = ""


def timeline_from_dict(payload: dict[str, Any]) -> TimelineMap:
    """Rebuild a `TimelineMap` from `TimelineMap.to_dict()` output.

    `to_dict()` also serialises derived properties (offset, confidence, residual), so
    only the real dataclass fields are passed through.
    """
    from ...align import Anchor

    data = dict(payload)
    anchors = [Anchor(**anchor) for anchor in data.pop("anchors", [])]
    return TimelineMap(
        a=data.get("a", 1.0),
        b=data.get("b", 0.0),
        anchors=anchors,
        ok=bool(data.get("ok", False)),
        reason=str(data.get("reason", "")),
        episode_duration_ms=int(data.get("episode_duration_ms", 0) or 0),
    )
