"""The mapreel script: an ordered list of map segments, each with its own narration."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...core.assets import Licence
from ...core.stage import StageOutput
from ...core.timeline import Timeline, WordSpan

Mode = Literal["build", "storyboard"]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _colour(value: str | None) -> str | None:
    if value is not None and not _HEX.match(value):
        raise ValueError("colours are #rrggbb")
    return value


class Item(BaseModel):
    """One thing shown on a place: a brand, a dish, an invention."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    say: str | None = Field(None, max_length=80)        # how the voice says it; matched instead of name
    image: str | None = Field(None, max_length=500)     # commons:File:…, https://…, or a local path
    emoji: str | None = Field(None, max_length=8)
    source: str | None = Field(None, max_length=500)
    at: float | None = Field(None, ge=0)                # seconds from segment start; overrides speech

    @field_validator("image")
    @classmethod
    def _image(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if "://" in value and not value.startswith(("http://", "https://")):
            raise ValueError("an image is 'commons:File:<name>', an http(s) URL or a local path")
        return value


class Camera(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zoom: float = Field(1.0, ge=0.5, le=3.0)
    hold: bool = False


class _Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narration: str = Field(min_length=1, max_length=400)
    camera: Camera = Field(default_factory=Camera)
    source: str | None = Field(None, max_length=500)
    colour: str | None = None

    @field_validator("colour")
    @classmethod
    def _check_colour(cls, value: str | None) -> str | None:
        return _colour(value)


class CountrySegment(_Segment):
    kind: Literal["country"]
    country: str = Field(min_length=2, max_length=60)
    items: list[Item] = Field(min_length=1, max_length=8)


class HighlightSegment(_Segment):
    kind: Literal["highlight"]
    countries: list[str] = Field(min_length=1, max_length=60)
    legend: str | None = Field(None, max_length=60)


class Stat(BaseModel):
    """A figure on a card: "70%", "$1.2 bn". Its first number counts up on screen."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=24)
    label: str | None = Field(None, max_length=40)
    source: str | None = Field(None, max_length=500)


class RankSegment(_Segment):
    kind: Literal["rank"]
    rank: int = Field(ge=1, le=99)
    country: str = Field(min_length=2, max_length=60)
    stat: Stat | None = None
    items: list[Item] = Field(default_factory=list, max_length=8)


class VersusSegment(_Segment):
    """Two or three countries side by side, one per panel, each with its stat."""

    kind: Literal["versus"]
    countries: list[str] = Field(min_length=2, max_length=3)
    stats: list[Stat] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def _one_stat_each(self) -> "VersusSegment":
        if len(self.stats) != len(self.countries):
            raise ValueError("versus needs one stat per country")
        # as written ("JPN" twice); a name and a code for one country ("Japan", "JPN") is caught on resolution
        written = [c.strip().casefold() for c in self.countries]
        if len(set(written)) != len(written):
            raise ValueError("versus needs different countries: one is listed twice")
        return self


class RegionEntry(BaseModel):
    """One state or prefecture of the segment's country, by name or ISO 3166-2 code, with its own items."""

    model_config = ConfigDict(extra="forbid")

    region: str = Field(min_length=1, max_length=60)
    say: str | None = Field(None, max_length=80)        # how the voice says it; it fills when this is heard
    items: list[Item] = Field(default_factory=list, max_length=6)
    colour: str | None = None

    @field_validator("colour")
    @classmethod
    def _check_colour(cls, value: str | None) -> str | None:
        return _colour(value)


class RegionsSegment(_Segment):
    """A country's states filling one by one as they are named, each carrying its own items."""

    kind: Literal["regions"]
    country: str = Field(min_length=2, max_length=60)
    regions: list[RegionEntry] = Field(min_length=1, max_length=12)

    @property
    def items(self) -> list[Item]:
        """Every region's items in order, as one list: image keys (`<segment id>/<index>`) count them so."""
        return [item for entry in self.regions for item in entry.items]


Segment = Annotated[Union[CountrySegment, HighlightSegment, RankSegment, VersusSegment, RegionsSegment],
                    Field(discriminator="kind")]


class MapReelInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = "build"
    title: str = Field("", max_length=80)
    language: str = "en"
    voice: str = "fake:default"
    look: Literal["satellite"] = "satellite"
    music: str | None = None  # "auto" (a synthesised bed) or a local audio file
    length: int | None = Field(None, ge=15, le=90)
    segments: list[Segment] = Field(min_length=1, max_length=40)
    name: str | None = None
    outdir: str | None = None

    def segment_ids(self) -> list[str]:
        return [f"seg-{index}" for index in range(1, len(self.segments) + 1)]


# -- stage payloads ------------------------------------------------------------------


class ResolvedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    index: int
    kind: str
    codes: list[str]
    names: dict[str, str]
    colours: dict[str, str]
    regions: list[dict] = Field(default_factory=list)  # a regions segment: {code, name, colour} per region


class ScriptOutput(StageOutput):
    inputs: MapReelInputs
    segments: list[ResolvedSegment]


class GeoOutput(ScriptOutput):
    world: str
    layers: dict[str, str]                 # segment id -> detail layer file
    detail_bboxes: dict[str, list[float]]
    frames: dict[str, dict[str, float]]    # segment id -> landed frame {x, y, s}
    # versus segment id -> one panel per country: {"frame": {x, y, s}, "layer": path, "bbox": [..], "rect": [x, y, w, h]}
    panels: dict[str, list[dict]] = Field(default_factory=dict)


class ImagesOutput(GeoOutput):
    images: dict[str, str | None]          # "<segment id>/<item index>" -> local file, or None for a badge
    warnings: list[str] = Field(default_factory=list)


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int
    duration_ms: int
    speech_offset_ms: int
    speech_ms: int


class NarrationOutput(ImagesOutput):
    audio_asset: str
    duration_ms: int
    windows: list[Window]
    words: list[list[WordSpan]]            # shown words, reel time
    hot: list[list[bool]]
    spoken: list[list[WordSpan]]           # the voice's own words, line time
    provider: str
    licence: Licence | None = None


class SegmentProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    index: int
    dir: str
    key: str
    nominal_ms: int


class SegmentsOutput(StageOutput):
    narration: NarrationOutput
    projects: list[SegmentProject]
    cues: list[dict] = Field(default_factory=list)


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    path: str
    render_ms: int


class PrepOutput(StageOutput):
    segments: SegmentsOutput
    tracks: list[Track] = Field(default_factory=list)
    storyboard: str = ""
    renderer: str = ""


class ComposeOutput(StageOutput):
    prep: PrepOutput
    timeline: Timeline
    render_options: dict = Field(default_factory=dict)


class RenderOutput(StageOutput):
    video: str
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = "hyperframes"
