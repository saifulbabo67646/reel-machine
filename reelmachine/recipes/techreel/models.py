"""The techreel script: beats of narrated lines, each with hero state and stage visuals."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reelmachine.core.stage import StageOutput

Look = Literal["mint", "ember", "violet", "mono"]
BeatKind = Literal["hook", "pain", "reveal", "explain", "feature", "honest", "cta", "other"]
Transition = Literal["cut", "slide", "flash", "zoom"]
HeroKind = Literal["orb", "laptop", "monitor"]
HeroState = Literal["center", "small-top", "corner", "hidden", "pulse", "shake", "rings", "screen", "sleep"]
Unit01 = Annotated[float, Field(ge=0, le=1)]

_ID = r"^[a-z][a-z0-9_-]{0,23}$"
_LANGUAGE = r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$"      # BCP-47 shape: primary subtag then subtags
_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"            # the output file's stem
_HEX = r"^#[0-9a-fA-F]{6}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cue(_Model):
    """A moment inside the narration: a line, optionally at one of its words or a fraction of it."""

    line: int = Field(ge=0)
    word: str | None = None
    frac: float | None = Field(None, ge=0, le=1)

    @model_validator(mode="after")
    def _one_anchor(self) -> Cue:
        if self.word is not None and self.frac is not None:
            raise ValueError("a cue gives word or frac, not both")
        return self


class Line(_Model):
    say: str = Field(min_length=1, max_length=400)
    text: str | None = Field(None, max_length=400)       # the caption; defaults to what is said

    @property
    def caption(self) -> str:
        return self.text or self.say


class VisualBase(_Model):
    at: Cue | None = None
    exit: Cue | Literal["beat"] = "beat"
    place: Literal["stack", "left", "right"] = "stack"


class HeroVisual(VisualBase):
    kind: Literal["hero"]
    state: HeroState
    text: str | None = Field(None, max_length=24)


class TitleVisual(VisualBase):
    kind: Literal["title"]
    text: str = Field(max_length=80)
    size: Literal["m", "l", "xl"] = "l"


class CounterVisual(VisualBase):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: Literal["counter"]
    from_: float = Field(0, alias="from")
    to: float
    decimals: int = Field(0, ge=0, le=2)
    prefix: str = Field("", max_length=8)
    suffix: str = Field("", max_length=12)
    label: str = Field("", max_length=40)


class StampVisual(VisualBase):
    kind: Literal["stamp"]
    text: str = Field(max_length=32)
    tone: Literal["accent", "red", "green"] = "red"
    rotate: float = Field(-8, ge=-25, le=25)


class EmojiVisual(VisualBase):
    kind: Literal["emoji"]
    emoji: str = Field(max_length=16)
    place: Literal["auto", "left", "right"] = "auto"     # overrides the base: emoji may float on its own


class ChipsVisual(VisualBase):
    kind: Literal["chips"]
    items: list[Annotated[str, Field(max_length=24)]] = Field(min_length=1, max_length=6)
    style: Literal["outline", "filled"] = "outline"


class CursorMove(_Model):
    to: tuple[Unit01, Unit01]
    at: Cue


class WindowVisual(VisualBase):
    kind: Literal["window"]
    title: str = Field(max_length=40)
    tabs: list[Annotated[str, Field(max_length=16)]] = Field(default_factory=list, max_length=4)
    active: int = 0
    live: bool = False
    cursor: list[CursorMove] = Field(default_factory=list, max_length=4)
    button: str | None = Field(None, max_length=24)
    # chat or list lines shown in the body; none gives placeholder bars
    rows: list[Annotated[str, Field(max_length=48)]] = Field(default_factory=list, max_length=4)


class TermLine(_Model):
    cmd: str | None = Field(None, max_length=80)
    out: str | None = Field(None, max_length=80)
    tone: Literal["ok", "err", "dim"] = "dim"

    @model_validator(mode="after")
    def _one_of(self) -> TermLine:
        if (self.cmd is None) == (self.out is None):
            raise ValueError("a terminal line gives exactly one of cmd or out")
        return self


class TerminalVisual(VisualBase):
    kind: Literal["terminal"]
    lines: list[TermLine] = Field(min_length=1, max_length=8)


class RepoVisual(VisualBase):
    kind: Literal["repo"]
    name: str = Field(max_length=60)
    description: str = Field("", max_length=140)
    stars: int = Field(0, ge=0)
    license: str = Field("", max_length=24)
    language: str = Field("", max_length=24)


Visual = Annotated[
    HeroVisual | TitleVisual | CounterVisual | StampVisual | EmojiVisual | ChipsVisual | WindowVisual | TerminalVisual | RepoVisual,
    Field(discriminator="kind"),
]


class HeroSpec(_Model):
    kind: HeroKind
    text: str | None = Field(None, max_length=24)


class Creator(_Model):
    name: str = Field(max_length=40)
    handle: str = Field(max_length=40)
    avatar: str | None = None


class Beat(_Model):
    id: str | None = Field(None, pattern=_ID)
    kind: BeatKind = "other"
    heading: str | None = Field(None, max_length=60)
    kicker: str | None = Field(None, max_length=40)
    lines: list[Line] = Field(min_length=1, max_length=8)
    visuals: list[Visual] = Field(default_factory=list, max_length=12)
    transition: Transition = "cut"
    keyword: str | None = Field(None, max_length=24)
    dm: str | None = Field(None, max_length=40)
    next: str | None = Field(None, max_length=60)


class TechReelInputs(_Model):
    title: str = Field(min_length=1, max_length=80)
    language: str = Field("en", pattern=_LANGUAGE)
    voice: str = "fake:default"
    look: Look = "mint"
    accent: str | None = Field(None, pattern=_HEX)
    hero: HeroSpec | None = None
    mode: Literal["storyboard", "full"] = "storyboard"
    lint: Literal["strict", "warn"] = "strict"
    music: str | None = None
    sfx: bool = True
    creator: Creator | None = None
    sources: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=20)
    beats: list[Beat] = Field(default_factory=list, max_length=40)
    name: str | None = Field(None, pattern=_NAME)
    outdir: str | None = None


# ---------------------------------------------------------------- stage outputs


class ScriptOutput(StageOutput):
    inputs: TechReelInputs
    lint: list[dict] = Field(default_factory=list)       # every finding, with its path
    warnings: list[str] = Field(default_factory=list)    # the findings that did not stop the build, as text


class LineSlotModel(_Model):
    """`schedule.LineSlot` as stage data."""

    start_ms: int
    speech_ms: int
    end_ms: int


class BeatSlotModel(_Model):
    """`schedule.BeatSlot` as stage data."""

    start_ms: int
    end_ms: int
    lines: list[LineSlotModel]


class NarrationOutput(StageOutput):
    script: ScriptOutput
    audio_asset: str
    duration_ms: int
    beats: list[BeatSlotModel]
    times: list[list[list[tuple[int, int]]]]             # per beat, per line, per caption unit; line-relative
    provider: str = ""


class ComposeOutput(StageOutput):
    narration: NarrationOutput
    project: str
    key: str


class PreviewOutput(StageOutput):
    compose: ComposeOutput
    sheet: str = ""                                      # storyboard.jpg; empty in full mode


class RenderOutput(StageOutput):
    video: str
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = "hyperframes"
