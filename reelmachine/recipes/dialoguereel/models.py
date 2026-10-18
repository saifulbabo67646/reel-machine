"""The dialoguereel script: a two-character cast and the lines they act out."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reelmachine.core.stage import StageOutput
from reelmachine.core.timeline import WordSpan

Look = Literal["pop"]
Mode = Literal["cast", "storyboard", "full"]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class BuiltinSource(BaseModel):
    """A character drawn by a built-in SVG template, optionally recoloured."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["builtin"]
    name: str = Field(min_length=1, max_length=40)
    colors: dict[Literal["primary", "secondary", "accent"], str] = Field(default_factory=dict)

    @field_validator("colors")
    @classmethod
    def _colors(cls, value: dict[str, str]) -> dict[str, str]:
        for colour in value.values():
            if not _HEX.match(colour):
                raise ValueError("colours are #rrggbb")
        return value


class SvgSource(BaseModel):
    """A character supplied as inline SVG."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["svg"]
    svg: str = Field(min_length=1, max_length=60_000)


CharacterSource = Annotated[BuiltinSource | SvgSource, Field(discriminator="kind")]


class Character(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,23}$")
    name: str = Field(min_length=1, max_length=40)
    voice: str = "fake:default"
    side: Literal["left", "right"]
    source: CharacterSource


class CastSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characters: list[Character]

    @model_validator(mode="after")
    def _two_distinct(self) -> CastSpec:
        chars = self.characters
        if len(chars) != 2:
            raise ValueError("a cast is exactly two characters")
        if chars[0].id == chars[1].id:
            raise ValueError("cast ids must be distinct")
        if chars[0].side == chars[1].side:
            raise ValueError("the two characters stand on opposite sides")
        return self


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str = Field(min_length=1, max_length=24)
    text: str = Field(min_length=1, max_length=80)
    reading: str | None = Field(None, max_length=120)
    meaning: str = Field(min_length=1, max_length=120)


class DialogueReelInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field("", max_length=60)
    language: str = "ja"
    meaning_language: str = "en"
    level: str = Field("", max_length=16)
    scene: str | None = Field(None, max_length=8)
    cast: str | CastSpec                              # a saved cast id, or the cast itself
    look: Look = "pop"
    mode: Mode = "storyboard"
    lint: Literal["strict", "warn"] = "strict"
    music: str | None = None
    name: str | None = None
    outdir: str | None = None
    lines: list[Line] = Field(default_factory=list, max_length=30)


class ResolvedCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    voice: str
    side: str
    svg: str                      # recoloured, not yet scoped
    parts: list[str]


class ResolvedCast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    characters: list[ResolvedCharacter]


# ---------------------------------------------------------------- stage outputs


class ScriptOutput(StageOutput):
    inputs: DialogueReelInputs
    cast: ResolvedCast
    lint: list[dict] = Field(default_factory=list)       # every finding, with its path
    warnings: list[str] = Field(default_factory=list)    # the findings that did not stop the build, as text


class LineWindowModel(BaseModel):
    """`schedule.LineWindow` as stage data."""

    model_config = ConfigDict(extra="forbid")

    start_ms: int
    speech_ms: int
    end_ms: int
    meaning_ms: int


class NarrationOutput(StageOutput):
    script: ScriptOutput
    audio_asset: str = ""                                # empty in cast mode: nothing is spoken
    duration_ms: int
    lines: list[LineWindowModel] = Field(default_factory=list)
    words: list[list[WordSpan]] = Field(default_factory=list)  # per line, in line time (as the voice said them)
    provider: str = ""


class ComposeOutput(StageOutput):
    narration: NarrationOutput
    project: str
    key: str


class PreviewOutput(StageOutput):
    compose: ComposeOutput
    sheet: str = ""                                      # cast.png or storyboard.jpg; empty in full mode


class RenderOutput(StageOutput):
    video: str
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = "hyperframes"
