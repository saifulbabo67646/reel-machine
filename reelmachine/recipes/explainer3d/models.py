"""Inputs and stage payloads for the explainer3d recipe.

The script is what an agent writes. Move parameters travel as extra fields on an action
or a camera and are validated against the move catalogue in `validate.py`, so the schema
stays small and every refusal can name the exact field.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...core.assets import Licence, Provenance
from ...core.stage import StageOutput
from ...core.timeline import Timeline, WordSpan

Mode = Literal["storyboard", "build"]
ShapeKind = Literal["bowl", "cup", "pot", "jar", "bottle", "ball", "box", "sack", "pile", "plinth"]
Material = Literal[
    "stone", "wood", "ceramic", "metal", "glass", "dough", "salt", "grain", "water", "cloth"
]
Environment = Literal["meadow-day", "studio", "lake", "underwater"]
StageKind = Literal["stone-slab", "table", "none"]

CAST_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MODEL_SOURCE = re.compile(r"^(polyhaven|khronos):[A-Za-z0-9_\-]{1,80}$")


def hex_colour(value: str | None) -> str | None:
    if value is None:
        return None
    if not HEX.match(value):
        raise ValueError("colours are #rrggbb")
    return value.lower()


class ShapeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: ShapeKind
    height: float = Field(0.3, gt=0, le=5)
    width: float | None = Field(None, gt=0, le=5)
    material: Material = "ceramic"
    colour: str | None = None
    count: int = Field(1, ge=1, le=12)  # >1: a row of identical pieces (bait balls)

    @field_validator("colour")
    @classmethod
    def check_colour(cls, value: str | None) -> str | None:
        return hex_colour(value)


class ModelSpec(BaseModel):
    """A CC0 model from the catalogue (`find_models`), scaled to `height` or `size`."""

    model_config = ConfigDict(extra="forbid")

    model: str
    height: float | None = Field(None, gt=0, le=20)
    size: float | None = Field(None, gt=0, le=20)  # the longest side; used when height is not given
    container: bool = False  # things can be dropped into it (a bowl, a bucket)
    count: int = Field(1, ge=1, le=12)  # >1: a school of fish, a row of buns
    colour: str | None = None  # tints a container's fill default and its crumbs

    @field_validator("model")
    @classmethod
    def check_source(cls, value: str) -> str:
        if not MODEL_SOURCE.match(value):
            raise ValueError("models are 'polyhaven:<id>' or 'khronos:<name>' (see find_models)")
        return value

    @field_validator("colour")
    @classmethod
    def check_colour(cls, value: str | None) -> str | None:
        return hex_colour(value)


class CastMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: ShapeSpec | ModelSpec
    at: str | None = None  # an anchor; None keeps the actor hidden until a move brings it

    @field_validator("asset", mode="before")
    @classmethod
    def _shorthand(cls, value: Any) -> Any:
        return {"model": value} if isinstance(value, str) else value


class Action(BaseModel):
    model_config = ConfigDict(extra="allow")

    move: str
    actor: str | None = None
    at: tuple[float, float] | None = None

    @model_validator(mode="after")
    def _window(self) -> "Action":
        if self.at is not None:
            start, end = self.at
            if not (0.0 <= start < end <= 1.0):
                raise ValueError("at is [start, end] with 0 <= start < end <= 1")
        return self

    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Camera(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    move: str = "static"
    target: str | None = None
    continue_: bool = Field(False, alias="continue")

    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    narration: str = Field("", max_length=240)  # markup: *hot* words, [shown|spoken]
    duration: float | None = Field(None, ge=0.8, le=8.0)  # seconds; required without narration
    label: str | None = Field(None, max_length=40)
    card: str | None = Field(None, max_length=40)  # a big end-card line ("SAVE THIS 📌")
    set: "ShotSet | None" = None  # switch environment for this shot (dive underwater)
    camera: Camera | None = None
    actions: list[Action] | Literal["auto"] = Field(default_factory=list)
    auto: list[Action] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_time(self) -> "Shot":
        if not self.narration.strip() and self.duration is None:
            raise ValueError("a shot needs narration, or a duration for a beat with no voice")
        return self


class SetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: Environment = "meadow-day"
    stage: StageKind = "stone-slab"


class ShotSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: Environment


Shot.model_rebuild()


class Explainer3DInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = "build"
    title: str = Field("", max_length=80)
    language: str = "en"
    voice: str = "fake:default"
    look: str = "diorama-day"
    music: str | None = None  # "auto" (a synthesised bed) or a local audio file
    length: int | None = Field(None, ge=15, le=90)  # target seconds; probe lints, build fits
    set: SetSpec = Field(default_factory=SetSpec)
    cast: dict[str, CastMember] = Field(default_factory=dict)
    shots: list[Shot] = Field(min_length=1, max_length=40)
    allow_fallback: bool = False
    audio_profile: str = "studio"
    name: str | None = None
    outdir: str | None = None

    @field_validator("cast")
    @classmethod
    def _cast_ids(cls, value: dict[str, CastMember]) -> dict[str, CastMember]:
        if len(value) > 30:
            raise ValueError("at most 30 cast members")
        bad = sorted(key for key in value if not CAST_ID.match(key))
        if bad:
            raise ValueError(f"cast ids match [a-z][a-z0-9_-]{{0,31}}: {bad}")
        return value

    def shot_list(self) -> list[Shot]:
        return [
            shot.model_copy(update={"id": shot.id or f"shot-{index}"})
            for index, shot in enumerate(self.shots, start=1)
        ]


class ResolvedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    move: str
    actor: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    at: tuple[float, float]
    index: int


class ResolvedCamera(BaseModel):
    model_config = ConfigDict(extra="forbid")

    move: str = "static"
    target: str = "stage.center"
    params: dict[str, Any] = Field(default_factory=dict)
    continues: bool = False


class ResolvedShot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    index: int
    narration: str
    label: str | None = None
    duration_ms: int | None = None
    card: str | None = None
    environment: str = "meadow-day"
    actions: list[ResolvedAction] = Field(default_factory=list)
    camera: ResolvedCamera = Field(default_factory=ResolvedCamera)


class ScriptOutput(StageOutput):
    inputs: Explainer3DInputs
    shots: list[ResolvedShot]


class ResolvedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["shape", "model"] = "shape"
    shape: ShapeSpec | None = None
    model: ModelSpec | None = None
    root: str = ""  # the cached model folder (models only)
    main: str = ""  # the .gltf/.glb inside it
    files: list[str] = Field(default_factory=list)
    bounds: tuple[list[float], list[float]] | None = None  # in the file's own units
    dims: tuple[float, float, float]
    licence: Licence
    provenance: Provenance


class EnvAssets(BaseModel):
    """A cinematic set's files (published path → cached file) and its dressing models."""

    model_config = ConfigDict(extra="forbid")

    files: dict[str, str] = Field(default_factory=dict)
    dressing: list[dict[str, Any]] = Field(default_factory=list)
    licences: list[Licence] = Field(default_factory=list)


class AssetsOutput(StageOutput):
    inputs: Explainer3DInputs
    shots: list[ResolvedShot]
    models: dict[str, ResolvedModel] = Field(default_factory=dict)
    envs: dict[str, EnvAssets] = Field(default_factory=dict)


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int
    duration_ms: int
    speech_offset_ms: int
    speech_ms: int


class NarrationOutput(StageOutput):
    inputs: Explainer3DInputs
    shots: list[ResolvedShot]
    models: dict[str, ResolvedModel] = Field(default_factory=dict)
    envs: dict[str, EnvAssets] = Field(default_factory=dict)
    audio_asset: str
    duration_ms: int
    windows: list[Window]
    words: list[list[WordSpan]]  # absolute ms, one list per shot: the words shown
    hot: list[list[bool]] = Field(default_factory=list)  # which shown words are highlighted
    voice: str
    provider: str
    licence: Licence | None = None


class ShotProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    index: int
    dir: str
    key: str
    nominal_ms: int
    render_ms: int
    join: Literal["none", "xfade", "cut"]


class ShotsOutput(StageOutput):
    narration: NarrationOutput
    projects: list[ShotProject]
    cues: list[dict[str, Any]] = Field(default_factory=list)


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    path: str
    render_ms: int
    renderer: str


class PrepOutput(StageOutput):
    shots: ShotsOutput
    tracks: list[Track] = Field(default_factory=list)
    storyboard: str = ""
    renderer: str = ""


class ComposeOutput(StageOutput):
    prep: PrepOutput
    timeline: Timeline
    render_options: dict[str, Any] = Field(default_factory=dict)


class RenderOutput(StageOutput):
    video: str
    ass: str = ""
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = "hyperframes"
