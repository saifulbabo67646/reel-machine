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
StageKind = Literal["stone-slab", "table", "counter", "none"]
Vec3 = tuple[float, float, float]

CAST_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MODEL_SOURCE = re.compile(r"^(polyhaven|khronos):[A-Za-z0-9_\-]{1,80}$")
POLYHAVEN_SOURCE = re.compile(r"^polyhaven:[A-Za-z0-9_\-]{1,80}$")


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


class PartSpec(BaseModel):
    """One primitive of a compound object. `size` is the part's own box (x, y, z) before it
    is rotated; `at` is its centre; `rotate` is in degrees about x, y, z."""

    model_config = ConfigDict(extra="forbid")

    shape: Literal["box", "cylinder", "sphere", "cone", "torus", "capsule", "plane"]
    size: tuple[float, float, float]
    at: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotate: tuple[float, float, float] = (0.0, 0.0, 0.0)
    material: Material = "ceramic"
    colour: str | None = None
    glow: float = Field(0.0, ge=0, le=10)  # emissive strength: screens, lamps, warning lights

    @field_validator("size")
    @classmethod
    def check_size(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        if any(v <= 0 or v > 20 for v in value):
            raise ValueError("every side of a part is between 0 and 20 m")
        return value

    @field_validator("colour")
    @classmethod
    def check_colour(cls, value: str | None) -> str | None:
        return hex_colour(value)


class CompoundSpec(BaseModel):
    """An object the agent builds from primitive parts, when no model fits."""

    model_config = ConfigDict(extra="forbid")

    parts: list[PartSpec] = Field(min_length=1, max_length=80)
    container: bool = False
    count: int = Field(1, ge=1, le=12)


class OutfitSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top: str = "#3a6ea5"
    bottom: str = "#2f3542"
    shoes: str = "#1e1e1e"
    sleeves: Literal["long", "short"] = "long"
    skin: str | None = None  # tints the skin texture (a darker or lighter tone)

    @field_validator("top", "bottom", "shoes", "skin")
    @classmethod
    def check_colour(cls, value: str | None) -> str | None:
        return hex_colour(value)


class CharacterSpec(BaseModel):
    """A stylised CC0 person (Quaternius) who can walk, talk, sit and handle props,
    dressed in an outfit of the script's colours."""

    model_config = ConfigDict(extra="forbid")

    character: Literal["quaternius:male", "quaternius:female", "mannequin"]
    height: float = Field(1.75, ge=0.3, le=3.0)
    outfit: OutfitSpec = Field(default_factory=OutfitSpec)
    hair: Literal["long", "buns", "parted", "buzzed", "buzzed_female", "none"] | None = None
    hair_colour: str = "#3b2a20"
    beard: bool = False

    @field_validator("hair_colour")
    @classmethod
    def check_colour(cls, value: str) -> str:
        return hex_colour(value)

    @model_validator(mode="after")
    def _default_hair(self) -> "CharacterSpec":
        if self.hair is None:
            self.hair = {"quaternius:female": "long", "quaternius:male": "parted"}.get(self.character, "none")
        return self


class ComponentSpec(BaseModel):
    """JavaScript the agent writes: `export function create({ THREE, params, rng, size })`
    returning `{ object?, update?(t, info) }` — a pure function of t (see the guide)."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=10, max_length=65536)


class ComponentAsset(BaseModel):
    """A cast member drawn by a component; `size` is its box, so moves can place it."""

    model_config = ConfigDict(extra="forbid")

    component: str
    size: tuple[float, float, float]
    params: dict[str, Any] = Field(default_factory=dict)
    container: bool = False
    count: int = 1

    @field_validator("size")
    @classmethod
    def check_size(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        if any(v <= 0 or v > 40 for v in value):
            raise ValueError("every side is between 0 and 40 m")
        return value


class EffectSpec(BaseModel):
    """A component drawn over a stretch of one shot (a laser, a chart, a magic burst)."""

    model_config = ConfigDict(extra="forbid")

    component: str
    at: tuple[float, float] = (0.0, 1.0)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _window(self) -> "EffectSpec":
        if not (0.0 <= self.at[0] < self.at[1] <= 1.0):
            raise ValueError("at is [start, end] with 0 <= start < end <= 1")
        return self


class CastMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: ShapeSpec | ModelSpec | CompoundSpec | CharacterSpec | ComponentAsset
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
    set: "SetSpec | None" = None  # this shot's set: a preset, or changes to the reel's set
    camera: Camera | None = None
    effects: list[EffectSpec] = Field(default_factory=list, max_length=8)
    actions: list[Action] | Literal["auto"] = Field(default_factory=list)
    auto: list[Action] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_time(self) -> "Shot":
        if not self.narration.strip() and self.duration is None:
            raise ValueError("a shot needs narration, or a duration for a beat with no voice")
        return self


def _polyhaven(value: str | None, what: str) -> str | None:
    if value is not None and not POLYHAVEN_SOURCE.match(value):
        raise ValueError(f"{what} are 'polyhaven:<id>' (see find_assets)")
    return value


class SurfaceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texture: str | None = None  # polyhaven:<texture>
    colour: str | None = None
    repeat: float = Field(4.0, gt=0, le=200)  # texture tiles across the surface

    @field_validator("texture")
    @classmethod
    def check_texture(cls, value: str | None) -> str | None:
        return _polyhaven(value, "textures")

    @field_validator("colour")
    @classmethod
    def check_colour(cls, value: str | None) -> str | None:
        return hex_colour(value)


class FloorSpec(SurfaceSpec):
    size: tuple[float, float] = (8.0, 8.0)


class WallsSpec(SurfaceSpec):
    height: float = Field(2.6, gt=0.2, le=20)
    sides: list[Literal["back", "left", "right", "front"]] = Field(default_factory=lambda: ["back", "left", "right"])


class WaterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    colour: str = "#0b2a2e"

    @field_validator("colour")
    @classmethod
    def check_colour(cls, value: str) -> str:
        return hex_colour(value)


class FogSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    colour: str = "#cfe6f5"
    density: float = Field(0.05, ge=0, le=1)

    @field_validator("colour")
    @classmethod
    def check_colour(cls, value: str) -> str:
        return hex_colour(value)


class PropSpec(BaseModel):
    """Set dressing: a model placed at a point or a named anchor/mark."""

    model_config = ConfigDict(extra="forbid")

    model: str
    at: Vec3 | str
    yaw: float = Field(0.0, ge=-360, le=360)  # degrees
    size: float | None = Field(None, gt=0, le=40)
    height: float | None = Field(None, gt=0, le=40)

    @field_validator("model")
    @classmethod
    def check_source(cls, value: str) -> str:
        if not MODEL_SOURCE.match(value):
            raise ValueError("models are 'polyhaven:<id>' or 'khronos:<name>' (see find_assets)")
        return value


class SetSpec(BaseModel):
    """Where a reel happens. A preset (lake, kitchen, …) or a set built from parts — a sky
    image for light and backdrop, a floor, walls, a stage, water, fog, props and named
    marks; fields given next to a preset override or extend it."""

    model_config = ConfigDict(extra="forbid")

    preset: str | None = None
    environment: str | None = None  # plan-1/2 name for the same thing as preset
    mode: Literal["normal", "underwater", "classic"] | None = None
    stage: StageKind | None = None
    stage_texture: str | None = None
    sky: str | None = None  # polyhaven:<hdri> — light and, by default, the backdrop
    background: str | None = None  # "sky", "blur" or #rrggbb
    floor: FloorSpec | None = None
    walls: WallsSpec | None = None
    water: WaterSpec | None = None
    fog: FogSpec | None = None
    props: list[PropSpec] = Field(default_factory=list, max_length=40)
    marks: dict[str, Vec3] = Field(default_factory=dict)
    sun_angle: float | None = Field(None, ge=-180, le=180)

    @field_validator("sky", "stage_texture")
    @classmethod
    def check_sources(cls, value: str | None) -> str | None:
        return _polyhaven(value, "skies and textures")

    @field_validator("background")
    @classmethod
    def check_background(cls, value: str | None) -> str | None:
        if value is None or value in ("sky", "blur"):
            return value
        return hex_colour(value)

    @field_validator("marks")
    @classmethod
    def check_marks(cls, value: dict[str, Vec3]) -> dict[str, Vec3]:
        bad = sorted(k for k in value if not CAST_ID.match(k))
        if bad:
            raise ValueError(f"mark names match [a-z][a-z0-9_-]{{0,31}}: {bad}")
        return value


ShotSet = SetSpec


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
    components: dict[str, ComponentSpec] = Field(default_factory=dict, max_length=20)
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
    environment: str = "meadow-day"  # the set's name (a preset, or "custom")
    set_key: str = ""  # which resolved set this shot uses
    actions: list[ResolvedAction] = Field(default_factory=list)
    camera: ResolvedCamera = Field(default_factory=ResolvedCamera)


class ScriptOutput(StageOutput):
    inputs: Explainer3DInputs
    shots: list[ResolvedShot]
    sets: dict[str, "ResolvedSet"] = Field(default_factory=dict)


class ResolvedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["shape", "model", "compound", "character", "component"] = "shape"
    shape: ShapeSpec | None = None
    model: ModelSpec | None = None
    compound: CompoundSpec | None = None
    character: CharacterSpec | None = None
    component: ComponentAsset | None = None
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
    sets: dict[str, "ResolvedSet"] = Field(default_factory=dict)
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
    sets: dict[str, "ResolvedSet"] = Field(default_factory=dict)
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


class ResolvedSet(BaseModel):
    """A set with every field decided: what the renderer builds."""

    model_config = ConfigDict(extra="forbid")

    name: str
    mode: Literal["normal", "underwater", "classic"] = "normal"
    stage: StageKind = "stone-slab"
    stage_texture: str | None = None
    sky: str | None = None
    background: str = "sky"
    floor: FloorSpec | None = None
    walls: WallsSpec | None = None
    water: WaterSpec | None = None
    fog: FogSpec | None = None
    props: list[PropSpec] = Field(default_factory=list)
    marks: dict[str, Vec3] = Field(default_factory=dict)
    sun_angle: float = 80.0

    @property
    def key(self) -> str:
        import hashlib
        import json

        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return f"{self.name}-{hashlib.sha256(blob.encode()).hexdigest()[:10]}"


ScriptOutput.model_rebuild()
AssetsOutput.model_rebuild()
NarrationOutput.model_rebuild()
