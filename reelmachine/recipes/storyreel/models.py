"""Inputs and stage payloads for the storyreel recipe.

The input model is the single source of truth for validation, the JSON Schema the CLI
and MCP serve, and every cache key. Stage payloads carry what the next stage needs;
cache keys reach into that payload's refreshed `inputs` with dotted paths
(`"inputs.language"`), so a request field is never copied onto an intermediate output —
a copy would go stale the moment a later job changed the input it came from.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...core.assets import Licence, Provenance
from ...core.stage import StageOutput
from ...core.timeline import Timeline, WordSpan

Aspect = Literal["vertical", "square", "landscape"]
AudioProfile = Literal["none", "studio", "mobile", "youtube", "tiktok"]

#: The workflow's content-type menu, plus "let the model pick the strongest angle".
ContentType = Literal[
    "emotional",
    "sad",
    "thriller",
    "mystery",
    "psychological",
    "horror",
    "action",
    "romance",
    "comedy",
    "survival",
    "revenge",
    "life-lesson",
    "motivational",
    "plot-twist",
    "character-journey",
    "ai-decide",
]

Platform = Literal[
    "tiktok",
    "instagram-reels",
    "facebook-reels",
    "youtube-shorts",
    "youtube-long",
]

Mode = Literal["transcript", "plan", "build"]
Zoom = Literal["none", "in", "out"]
Transition = Literal["cut", "fade"]
Fit = Literal["crop", "band"]

MEDIA_TYPES = ("movie", "tv")


class MicroClipSpec(BaseModel):
    """One short supporting shot, taken from a precise window of the film."""

    model_config = ConfigDict(extra="forbid")

    #: The transcript scene this clip shows (e.g. "s0042"). When set, the engine
    #: keeps `source_start_ms` inside that scene's subtitle window, so a clip can
    #: never be cut from a moment the scene index does not cover.
    scene_ref: str = ""
    source_start_ms: int = Field(default=0, ge=0)
    source_end_ms: int | None = Field(default=None, ge=0)
    scene: str = ""
    zoom: Zoom = "none"
    text_overlay: str = ""
    transition: Transition = "cut"

    @property
    def suggested_ms(self) -> int:
        if self.source_end_ms is None:
            return 4_000
        return max(0, self.source_end_ms - self.source_start_ms)


class StorySection(BaseModel):
    """One voiceover line and the shot that carries it."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    voiceover: str
    clip: MicroClipSpec


class SeoPackage(BaseModel):
    """Always English unless the caller explicitly says otherwise."""

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    hook: str = ""
    cta: str = ""


class StoryConcept(BaseModel):
    """A whole storytelling opportunity, not a scene."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    name: str
    angle: str = ""
    why_viral: str = ""
    emotional_score: int = Field(default=5, ge=1, le=10)
    viral_score: int = Field(default=5, ge=1, le=10)
    retention_score: int = Field(default=5, ge=1, le=10)
    characters: list[str] = Field(default_factory=list)
    summary: str = ""
    audience_reaction: str = ""


class StoryPackage(BaseModel):
    """The final creative package: the sections, the clip plan and the SEO."""

    model_config = ConfigDict(extra="forbid")

    concept_id: str = ""
    concept_name: str = ""
    viral_reason: str = ""
    sections: list[StorySection]
    seo: SeoPackage | None = None


class StoryReelInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- step 1: what film -----------------------------------------------------
    title: str | None = None
    tmdb_id: int | None = None
    media_type: Literal["movie", "tv"] = "movie"
    season: int = Field(default=1, ge=1)
    episode: int = Field(default=1, ge=1)
    year: int | None = None

    # --- steps 2-5: the creative brief -----------------------------------------
    content_type: ContentType = "ai-decide"
    language: str = "hi"
    platform: Platform = "tiktok"
    target_ms: int = Field(default=300_000, ge=15_000, le=1_800_000)
    mode: Mode = "build"

    # --- step 6: the subtitle script -------------------------------------------
    subtitle_path: str | None = None
    subtitle_language: str = "en"
    subtitle_offset_ms: int = Field(default=0, ge=-120_000, le=120_000)

    # --- steps 8-10: the creative work -----------------------------------------
    concept: str | None = None
    concepts: list[StoryConcept] | None = None
    story: StoryPackage | None = None

    # --- production -------------------------------------------------------------
    #: `<provider>:<voice-id>`; empty means the deployment's story voice
    #: (`REEL_STORY_VOICE`), falling back to the deterministic fake voice.
    voice: str = ""
    music: str | None = None
    aspect: Aspect = "vertical"
    #: empty means the pack for the voiceover language (`storyreel.<lang>`) when one
    #: ships, else `storyreel.default`
    style: str = ""
    fit: Fit = "crop"
    audio_profile: AudioProfile = "tiktok"
    name: str | None = None
    outdir: str | None = None

    def wants_transcript_only(self) -> bool:
        return self.mode == "transcript"

    def wants_plan_only(self) -> bool:
        return self.mode == "plan"


class ResolvedMedia(BaseModel):
    """Which film, once TMDB (and the caller's choice) have settled it."""

    model_config = ConfigDict(extra="forbid")

    tmdb_id: int
    media_type: str = "movie"
    season: int = 1
    episode: int = 1
    title: str = ""
    year: int | None = None
    original_language: str = ""
    runtime_min: int = 0
    genres: list[str] = Field(default_factory=list)
    overview: str = ""
    candidates: list[dict[str, Any]] = Field(default_factory=list)

    def label(self) -> str:
        if self.media_type == "movie":
            return f"{self.title}" + (f" ({self.year})" if self.year else "")
        return f"{self.title} S{self.season:02d}E{self.episode:02d}"


class AcquireOutput(StageOutput):
    inputs: StoryReelInputs
    media: ResolvedMedia
    video_path: str = ""          # the film on disk (cached in REEL_VIDVAULT_DIR)
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    provider_label: str = ""
    subtitle_tracks: list[dict[str, Any]] = Field(default_factory=list)
    source_meta: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    start_ms: int
    end_ms: int
    text: str


class SceneChunk(BaseModel):
    """A run of dialogue the model reasons over (and the clip plan references)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    start_ms: int
    end_ms: int
    text: str
    cues: int = 0


class SubtitlesOutput(StageOutput):
    inputs: StoryReelInputs
    media: ResolvedMedia
    video_path: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    path: str = ""                # normalised UTF-8 SRT in the stage workdir
    asset: str = ""               # AssetKind.SUBTITLE id
    source: str = ""              # caller | embedded | subdl | opensubtitles
    language: str = ""
    cues: list[Cue] = Field(default_factory=list)
    span_start_ms: int = 0
    span_end_ms: int = 0
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    synced: bool = False
    offset_ms: int = 0
    provenance: Provenance = Field(default_factory=Provenance)
    licence: Licence | None = None
    warnings: list[str] = Field(default_factory=list)


class TranscriptOutput(StageOutput):
    inputs: StoryReelInputs
    media: ResolvedMedia
    video_path: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    srt_path: str = ""
    subtitle_source: str = ""
    subtitle_language: str = ""
    synced: bool = False
    cues: list[Cue] = Field(default_factory=list)
    scenes: list[SceneChunk] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ConceptsOutput(StageOutput):
    inputs: StoryReelInputs
    media: ResolvedMedia
    video_path: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    scenes: list[SceneChunk] = Field(default_factory=list)
    concepts: list[StoryConcept] = Field(default_factory=list)
    source: str = ""              # caller | brain | supplied-story
    warnings: list[str] = Field(default_factory=list)


class ScriptOutput(StageOutput):
    inputs: StoryReelInputs
    media: ResolvedMedia
    video_path: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    story: StoryPackage
    #: The subtitle text of every scene the story references (id → dialogue), carried
    #: so the edit plan can show "this line, over that dialogue" for review.
    scene_index: dict[str, str] = Field(default_factory=dict)
    source: str = ""              # caller | brain
    warnings: list[str] = Field(default_factory=list)


class SectionTiming(BaseModel):
    """One section's slot on the reel clock, after its voiceover is known."""

    model_config = ConfigDict(extra="forbid")

    id: str
    start_ms: int
    end_ms: int
    words: list[WordSpan] = Field(default_factory=list)
    markers: list[str] = Field(default_factory=list)


class VoiceoverOutput(StageOutput):
    inputs: StoryReelInputs
    media: ResolvedMedia
    video_path: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    story: StoryPackage
    scene_index: dict[str, str] = Field(default_factory=dict)
    audio_asset: str = ""
    narration_ms: int = 0
    sections: list[SectionTiming] = Field(default_factory=list)
    timings_source: str = ""      # provider | mixed | proportional
    provider: str = ""
    provenance: Provenance = Field(default_factory=Provenance)
    licence: Licence | None = None
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list)


class ClipRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: str
    asset: str
    start_ms: int                # on the reel clock
    duration_ms: int
    source_start_ms: int = 0
    zoom: str = "none"


class PrepOutput(StageOutput):
    inputs: StoryReelInputs
    media: ResolvedMedia
    video_path: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    story: StoryPackage
    scene_index: dict[str, str] = Field(default_factory=dict)
    audio_asset: str = ""
    narration_ms: int = 0
    sections: list[SectionTiming] = Field(default_factory=list)
    clips: list[ClipRecord] = Field(default_factory=list)
    frame: tuple[int, int] = (1080, 1920)
    clip_height: int = 1080
    provider: str = ""            # the voice provider that spoke the narration
    warnings: list[str] = Field(default_factory=list)


class StoryCompose(StageOutput):
    inputs: StoryReelInputs
    timeline: Timeline
    plan: dict[str, Any] = Field(default_factory=dict)
    render_options: dict[str, Any] = Field(default_factory=dict)


class StoryRenderOutput(StageOutput):
    video: str
    ass: str
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = "story"
