"""Inputs and stage payloads for the doodle recipe.

One recipe, two render modes: `stroke` (whiteboard inking over line art) and `program`
(deterministic knowledge graphics). The render mode is declared data, carried into the
manifest, so a static pan or an SVG fly-in can never be reported as stroke animation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...core.assets import Licence, Provenance
from ...core.stage import StageOutput
from ...core.timeline import Timeline, WordSpan

DoodleMode = Literal["stroke", "program"]
SceneStructure = Literal["auto", "single", "multi_act", "dual_islands"]
Aspect = Literal["vertical", "landscape"]


class Beat(BaseModel):
    """One narration beat: what is said, and the precise text it puts on screen."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    narration: str
    keywords: list[str] = Field(default_factory=list)
    art: str | None = None  # supplied line art for this beat (stroke)
    structure: SceneStructure | None = None


class DoodleInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str | None = None
    script: list[Beat] | None = None
    mode: DoodleMode = "stroke"

    language: str = "en"
    voice: str = "fake:default"
    allow_visible_fallback: bool = False

    line_art: list[str] = Field(default_factory=list)
    style: str = "doodle.default"
    aspect: Aspect = "vertical"
    scene_structure: SceneStructure = "auto"
    audio_profile: str = "studio"
    preflight: bool = True
    captions: bool = True

    name: str | None = None
    outdir: str | None = None

    def beat_list(self) -> list[Beat]:
        if self.script:
            return [
                beat.model_copy(update={"id": beat.id or f"beat-{index}"})
                for index, beat in enumerate(self.script, start=1)
            ]
        raise ValueError("no script; the script stage fills one from the topic")


class ScriptOutput(StageOutput):
    inputs: DoodleInputs
    beats: list[Beat] = Field(default_factory=list)
    source: str = ""


# ------------------------------------------------------------------- narration

class NarrationSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    beat_id: str
    text: str
    start_ms: int
    end_ms: int
    words: list[WordSpan] = Field(default_factory=list)


class NarrationOutput(StageOutput):
    inputs: DoodleInputs
    beats: list[Beat] = Field(default_factory=list)
    audio_asset: str = ""
    duration_ms: int = 0
    timings_source: str = ""  # "provider" | "proportional" (line-level voices)
    segments: list[NarrationSegment] = Field(default_factory=list)
    voice: str = ""
    provider: str = ""
    provenance: Provenance = Field(default_factory=Provenance)
    licence: Licence | None = None
    degraded: bool = False
    degradation_note: str = ""


# ---------------------------------------------------------------------- scenes

class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    width: int
    height: int

    def scaled(self, factor: float) -> "Region":
        return Region(
            x=int(round(self.x * factor)),
            y=int(round(self.y * factor)),
            width=max(1, int(round(self.width * factor))),
            height=max(1, int(round(self.height * factor))),
        )


class SceneRegion(BaseModel):
    """A semantic island of a stroke scene, revealed on the narration's clock."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str = ""
    sequence: int = 1
    region: Region
    start_ms: int = 0
    duration_ms: int = 1000
    protected: list[Region] = Field(default_factory=list)


class SceneElement(BaseModel):
    """A typed element of a program scene. Deterministic: text is authored, never guessed."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["card", "arrow", "label", "figure", "text"]
    text: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    enter_ms: int = 0
    exit_ms: int = 0
    enter: Literal["fade", "draw", "slide"] = "fade"
    style_ref: str = ""
    points: list[tuple[int, int]] = Field(default_factory=list)  # arrows: polyline


class SceneSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    index: int
    mode: DoodleMode
    structure: str = "single"
    narration: str
    duration_ms: int
    canvas_w: int
    canvas_h: int
    line_art_asset: str | None = None
    regions: list[SceneRegion] = Field(default_factory=list)
    elements: list[SceneElement] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class ScenesOutput(StageOutput):
    inputs: DoodleInputs
    scenes: list[SceneSpec] = Field(default_factory=list)
    schedule_source: str = "narration"  # never "even-division"
    narration: "NarrationOutput | None" = None


class SceneTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    index: int
    asset: str
    path: str = ""
    duration_ms: int = 0
    renderer: str = ""


class PrepOutput(StageOutput):
    inputs: DoodleInputs
    beats: list[Beat] = Field(default_factory=list)
    narration: "NarrationOutput | None" = None
    tracks: list[SceneTrack] = Field(default_factory=list)
    timeline: Timeline | None = None  # compose builds it from the tracks
    duration_ms: int = 0
    manifest_items: list[dict[str, Any]] = Field(default_factory=list)
    render_options: dict[str, Any] = Field(default_factory=dict)
    preflight: dict[str, Any] = Field(default_factory=dict)


class DoodleCompose(StageOutput):
    inputs: DoodleInputs
    timeline: Timeline
    render_options: dict[str, Any] = Field(default_factory=dict)


class DoodleRenderOutput(StageOutput):
    video: str
    ass: str
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = ""
