"""Inputs and stage payloads for the wordreel recipe.

The input model is the single source of truth for validation, the JSON Schema the CLI
and MCP serve, and every cache key — a stage's cache fields reach into this model with
dotted paths, never into a copy of it.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...core.stage import StageOutput
from ...core.timeline import Timeline, WordSpan
from .clips import ClipCandidate

Aspect = Literal["vertical", "square", "original"]

#: `suggest` proposes words the library can teach; `scan` lists the example lines;
#: `plan` stops before any cut; `build` renders the reel.
Mode = Literal["suggest", "scan", "plan", "build"]
MatchMode = Literal["smart", "exact", "regex"]


class WordReelInputs(BaseModel):
    """Everything a caller can ask of this recipe."""

    model_config = ConfigDict(extra="forbid")

    #: The word or phrase to teach. Empty in `suggest` mode — the corpus proposes.
    word: str = ""
    mode: Mode = "build"
    match_mode: MatchMode = "smart"
    #: Accept an inflected form (`gave up` for `give up`). Off makes the search literal.
    allow_inflected: bool = True

    #: Where the examples come from. Empty falls back to REEL_WORDREEL_LIBRARY.
    library: list[str] = Field(default_factory=list)
    #: Explicit subtitle files — a scan with no footage behind it still lists examples.
    subtitles: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=lambda: ["en"])

    #: How many example clips the reel holds, and how hard to spread them across
    #: titles. `per_source` is a preference: the shortlist aims for that many lines
    #: per title and then fills any remaining slots, so a one-film library still
    #: produces a full reel instead of a single clip.
    count: int = 6
    per_source: int = 1
    #: Shown under the word for the first seconds. The caller (often an agent) supplies
    #: the meaning; the recipe never guesses one.
    gloss: str = ""

    aspect: Aspect | None = None
    name: str | None = None
    outdir: str | None = None
    watermark: str = ""
    include_card: bool = True
    pre_roll_ms: int | None = None
    post_roll_ms: int | None = None
    min_clip_ms: int | None = None
    max_clip_ms: int | None = None

    def normalised_word(self) -> str:
        return " ".join(self.word.split())

    def request_languages(self) -> list[str]:
        return [lang for lang in self.languages if lang] or ["en"]


# ------------------------------------------------------------------- stage payloads


class SelectOutput(StageOutput):
    """The target, plus what the corpus could offer when the caller did not choose."""

    inputs: WordReelInputs
    word: str = ""
    corpus: str = ""
    directories: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class ScanOutput(StageOutput):
    """The shortlist: every example line, ranked, before anything is cut."""

    inputs: WordReelInputs
    word: str = ""
    corpus: str = ""
    candidates: list[ClipCandidate] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    plan: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)


class ClipRecord(BaseModel):
    """One cut example clip, with everything the render needs."""

    model_config = ConfigDict(extra="forbid")

    index: int                 # 1-based position in the reel
    element_id: str
    candidate: ClipCandidate
    clip: str                  # path to the cut file
    clip_asset: str = ""       # asset id of the same bytes
    start_ms: int = 0          # position on the reel timeline
    duration_ms: int = 0
    words: list[WordSpan] = Field(default_factory=list)  # the line, word by word
    emphasis: str = ""         # the form to highlight inside `candidate.text`


class PrepOutput(StageOutput):
    timeline: Timeline
    word: str = ""
    clips: list[ClipRecord] = Field(default_factory=list)
    duration_ms: int = 0
    render_options: dict[str, Any] = Field(default_factory=dict)
    skipped: list[dict[str, Any]] = Field(default_factory=list)


class RenderOutput(StageOutput):
    video: str
    ass: str
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = "wordreel"
    timeline_digest: str = ""


def element_id(candidate: ClipCandidate, index: int) -> str:
    """A stable id for one example — the same line at the same time is the same clip."""
    blob = f"{candidate.media_path}|{candidate.start_ms}|{candidate.end_ms}|{candidate.text}"
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
    return f"wex-{index:03d}-{digest}"
