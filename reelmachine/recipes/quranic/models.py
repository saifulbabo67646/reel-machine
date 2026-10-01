"""Inputs and stage payloads for the quranic recipe.

No source media, no Nadeshiko, no alignment: a verse range, a reciter, a translation, a
style and a background become a timeline of recitation audio, word-level highlight
captions and a background.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from ...core.assets import Licence, Provenance
from ...core.stage import StageOutput
from ...core.timeline import Timeline, WordSpan

Aspect = Literal["vertical", "square", "landscape"]
AudioProfile = Literal["none", "studio", "mobile", "youtube", "tiktok"]


class GradientBackground(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["gradient"] = "gradient"
    colors: list[str] = Field(default_factory=list)  # empty → the style palette
    speed: float = 0.02
    angle_deg: float = 135.0


class FileBackground(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["file"] = "file"
    path: str
    fit: Literal["cover", "contain"] = "cover"


BackgroundChoice = Annotated[Union[GradientBackground, FileBackground], Field(discriminator="kind")]


class QuranicInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surah: int = Field(default=1, ge=1, le=114)
    ayah_start: int = Field(default=1, ge=1)
    ayah_end: int | None = Field(default=None, ge=1)

    corpus: str = "quran_com"  # quran_com | alquran_cloud | quran-fake
    reciter: str = "alafasy"
    translation: str = "en.sahih"

    style: str = "quranic.parchment"
    background: BackgroundChoice = Field(default_factory=GradientBackground)
    aspect: Aspect = "vertical"
    audio_profile: AudioProfile = "studio"
    word_highlight: bool = True
    show_translation: bool = True

    name: str | None = None
    outdir: str | None = None

    def ayah_range(self) -> tuple[int, int]:
        start = self.ayah_start
        end = self.ayah_end if self.ayah_end is not None else self.ayah_start
        if end < start:
            start, end = end, start
        return start, end


class AyahMaterial(BaseModel):
    """One ayah as the corpus serves it: text, translation, audio and any timings."""

    model_config = ConfigDict(extra="forbid")

    surah: int
    ayah: int
    text: str
    words: list[str] = Field(default_factory=list)
    translation: str = ""
    audio_url: str = ""
    word_timings: list[WordSpan] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.surah}:{self.ayah}"


class QuranSelection(StageOutput):
    inputs: QuranicInputs
    ayahs: list[AyahMaterial] = Field(default_factory=list)
    reciter_name: str = ""
    translation_name: str = ""
    provenance: Provenance = Field(default_factory=Provenance)
    licence: Licence | None = None
    timings_source: str = ""  # "provider" | "proportional" | "mixed"


class AyahAudio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ayah: int
    asset: str
    duration_ms: int
    source_url: str = ""


class QuranPrep(StageOutput):
    inputs: QuranicInputs
    style_id: str = ""
    audio_asset: str = ""
    audio_duration_ms: int = 0
    background_asset: str = ""
    background_origin: str = "gradient"
    ayah_windows: list[tuple[int, int, int]] = Field(default_factory=list)  # (ayah, start, end)
    words: list[WordSpan] = Field(default_factory=list)  # global, in recitation order
    ayahs: list[AyahMaterial] = Field(default_factory=list)
    audio: list[AyahAudio] = Field(default_factory=list)
    timings_source: str = ""  # "provider" | "mixed" | "proportional"


class QuranCompose(StageOutput):
    inputs: QuranicInputs
    timeline: Timeline
    render_options: dict[str, Any] = Field(default_factory=dict)


class QuranRenderOutput(StageOutput):
    video: str
    ass: str
    srt: str
    manifest: str
    duration_ms: int
    render_mode: str = "quranic"
