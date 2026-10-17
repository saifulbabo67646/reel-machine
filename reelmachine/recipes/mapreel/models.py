"""The mapreel script: an ordered list of map segments, each with its own narration."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


Segment = Annotated[Union[CountrySegment, HighlightSegment], Field(discriminator="kind")]


class MapReelInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = "build"
    title: str = Field("", max_length=80)
    language: str = "en"
    voice: str = "fake:default"
    look: Literal["satellite"] = "satellite"
    music: str | None = None
    length: int | None = Field(None, ge=15, le=90)
    segments: list[Segment] = Field(min_length=1, max_length=40)
    name: str | None = None
    outdir: str | None = None

    def segment_ids(self) -> list[str]:
        return [f"seg-{index}" for index in range(1, len(self.segments) + 1)]
