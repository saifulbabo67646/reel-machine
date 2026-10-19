"""The techreel script: beats of narrated lines, each with hero state and stage visuals."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reelmachine.core.stage import StageOutput

Look = Literal["mint", "ember", "violet", "mono"]
BeatKind = Literal["hook", "pain", "reveal", "explain", "feature", "honest", "cta", "other"]
Transition = Literal["cut", "slide", "flash", "zoom"]
HeroKind = Literal["orb", "laptop", "monitor", "chip", "phone", "robot", "svg", "image"]
HeroState = Literal["center", "small-top", "corner", "hidden", "pulse", "shake", "rings", "screen", "sleep", "locked", "split"]
Unit01 = Annotated[float, Field(ge=0, le=1)]

_ID = r"^[a-z][a-z0-9_-]{0,23}$"
_LANGUAGE = r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$"      # BCP-47 shape: primary subtag then subtags
_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"            # the output file's stem
_HEX = r"^#[0-9a-fA-F]{6}$"
KEYWORD_MAX = 24                                         # a comment keyword, authored or generated


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
    count: int = Field(3, ge=2, le=6)                    # how many copies a `split` makes
    labels: list[Annotated[str, Field(max_length=16)]] = Field(default_factory=list, max_length=6)


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


class EquationVisual(VisualBase):
    kind: Literal["equation"]
    rows: list[Annotated[str, Field(max_length=40)]] = Field(min_length=1, max_length=4)
    result: str = Field(max_length=24)


class ChatMessage(_Model):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    sender: str = Field(max_length=24, alias="from")     # `from` is a Python keyword
    text: str = Field(max_length=120)
    bot: bool = False


class ChatVisual(VisualBase):
    kind: Literal["chat"]
    channel: str = Field("", max_length=32)
    messages: list[ChatMessage] = Field(min_length=1, max_length=5)


class DialogVisual(VisualBase):
    kind: Literal["dialog"]
    title: str = Field(max_length=40)
    body: str = Field("", max_length=120)
    approve: str = Field("Approve", max_length=16)
    decline: str = Field("Decline", max_length=16)
    click: Literal["approve", "decline"] | None = None
    click_at: Cue | None = None

    @model_validator(mode="after")
    def _click_at_needs_click(self) -> DialogVisual:
        # a cue with no button to press would silently press nothing
        if self.click_at is not None and self.click is None:
            raise ValueError("click_at needs click")
        return self


class ErrorVisual(VisualBase):
    kind: Literal["error"]
    title: str = Field(max_length=40)
    body: str = Field(max_length=120)
    button: str = Field("OK", max_length=12)


class ReceiptItem(_Model):
    title: str = Field(max_length=32)
    sub: str = Field("", max_length=32)


class ReceiptsVisual(VisualBase):
    kind: Literal["receipts"]
    items: list[ReceiptItem] = Field(min_length=1, max_length=5)


class ChecklistVisual(VisualBase):
    kind: Literal["checklist"]
    items: list[Annotated[str, Field(max_length=40)]] = Field(min_length=1, max_length=6)
    state: Literal["done", "progress"] = "done"


class PeopleVisual(VisualBase):
    kind: Literal["people"]
    total: int = Field(ge=1, le=200)
    lit: int = Field(ge=0)
    label: str = Field("", max_length=40)
    badge: str = Field("", max_length=24)

    @model_validator(mode="after")
    def _lit_within_total(self) -> PeopleVisual:
        if self.lit > self.total:
            raise ValueError("lit cannot exceed total")
        return self


class Score(_Model):
    label: str = Field(max_length=20)
    value: str = Field(max_length=12)
    emoji: str = Field("", max_length=16)


class VersusVisual(VisualBase):
    kind: Literal["versus"]
    left: Score
    right: Score


class NodeItem(_Model):
    label: str = Field(max_length=20)
    icon: str = Field("", max_length=16)


class NodesVisual(VisualBase):
    kind: Literal["nodes"]
    items: list[NodeItem] = Field(min_length=2, max_length=6)
    layout: Literal["row", "triangle", "ring", "flow"] = "row"


class BarItem(_Model):
    label: str = Field(max_length=20)
    value: float = Field(ge=0)


class BarsVisual(VisualBase):
    kind: Literal["bars"]
    items: list[BarItem] = Field(min_length=2, max_length=6)
    unit: str = Field("", max_length=8)
    highlight: int | None = None

    @model_validator(mode="after")
    def _highlight_within_items(self) -> BarsVisual:
        if self.highlight is not None and not 0 <= self.highlight < len(self.items):
            raise ValueError(f"highlight must be an item index, 0 to {len(self.items) - 1}")
        return self


class FlashVisual(VisualBase):
    kind: Literal["flash"]
    text: str = Field(max_length=40)


class HeartbeatVisual(VisualBase):
    kind: Literal["heartbeat"]
    label: str = Field("", max_length=40)


class CommentVisual(VisualBase):
    kind: Literal["comment"]
    keyword: str = Field(max_length=KEYWORD_MAX)
    reply: str = Field("", max_length=40)


class FollowVisual(VisualBase):
    kind: Literal["follow"]                              # draws the reel's `creator`


class ImageVisual(VisualBase):
    kind: Literal["image"]
    src: str = Field(min_length=1, max_length=500)
    highlight: tuple[Unit01, Unit01, Unit01, Unit01] | None = None


class SvgVisual(VisualBase):
    kind: Literal["svg"]
    svg: str = Field(min_length=1, max_length=60000)
    size: Literal["s", "m", "l"] = "m"


Visual = Annotated[
    HeroVisual | TitleVisual | CounterVisual | StampVisual | EmojiVisual | ChipsVisual | WindowVisual
    | TerminalVisual | RepoVisual | EquationVisual | ChatVisual | DialogVisual | ErrorVisual | ReceiptsVisual
    | ChecklistVisual | PeopleVisual | VersusVisual | NodesVisual | BarsVisual | FlashVisual | HeartbeatVisual
    | CommentVisual | FollowVisual | ImageVisual | SvgVisual,
    Field(discriminator="kind"),
]


class HeroSpec(_Model):
    kind: HeroKind
    text: str | None = Field(None, max_length=24)
    svg: str | None = Field(None, max_length=60000)
    src: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _art_matches_kind(self) -> HeroSpec:
        if (self.svg is not None) != (self.kind == "svg"):
            raise ValueError("`svg` is given when, and only when, the hero kind is svg")
        if (self.src is not None) != (self.kind == "image"):
            raise ValueError("`src` is given when, and only when, the hero kind is image")
        return self


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
    keyword: str | None = Field(None, max_length=KEYWORD_MAX)
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


class AssetsOutput(StageOutput):
    script: ScriptOutput
    images: dict[str, str] = Field(default_factory=dict)   # each image `src` → its file name in `folder`
    folder: str = ""


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
    images: dict[str, str] = Field(default_factory=dict)   # the assets stage's, passed on to compose
    assets: str = ""                                     # the folder those files are in


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
