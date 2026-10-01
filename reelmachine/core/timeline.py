"""The Timeline: serializable data describing a reel.

A timeline is a duration plus tracks of elements. Composition emits *declarative* video
elements; media prep materialises every one of them into a playable `clip`; rendering
turns the materialised timeline, its assets and a style into a video file.

Nothing here knows about Nadeshiko, ffmpeg or any recipe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .assets import Licence, Provenance

Aspect = Literal["vertical", "square", "landscape"]

_STRICT = ConfigDict(extra="forbid")


class _Element(BaseModel):
    model_config = _STRICT

    element_id: str
    start_ms: int = 0


# --------------------------------------------------------------------------- video

class SourceCut(_Element):
    """A window of a source file, to be cut by media prep."""

    kind: Literal["source_cut"] = "source_cut"
    asset: str
    src_in_ms: int
    src_out_ms: int
    keep_audio: bool = True

    @property
    def duration_ms(self) -> int:
        return max(0, self.src_out_ms - self.src_in_ms)


class Clip(_Element):
    """A playable asset placed on the timeline — what prep produces."""

    kind: Literal["clip"] = "clip"
    asset: str
    duration_ms: int
    origin: Literal["cut", "scene", "background", "generated"] = "generated"
    keep_audio: bool = False


class GradientSpec(BaseModel):
    model_config = _STRICT

    colors: list[str] = Field(default_factory=lambda: ["#0e1621", "#1f3b4d"])
    angle_deg: float = 135.0
    animated: bool = True


class Background(_Element):
    """A background to be materialised: a generated gradient, a local file or an asset."""

    kind: Literal["background"] = "background"
    duration_ms: int
    asset: str | None = None
    path: str | None = None
    gradient: GradientSpec | None = None
    loop: bool = True
    fit: Literal["cover", "contain"] = "cover"


class ImageSequence(_Element):
    kind: Literal["image_sequence"] = "image_sequence"
    frames_dir: str
    fps: int = 30
    duration_ms: int


class GeneratedScene(_Element):
    """A declarative scene for a named renderer (doodle stroke / program).

    The core does not know what is inside `spec`; the renderer registered under
    `renderer` does. `render_mode` is what the manifest will declare this element as.
    """

    kind: Literal["generated_scene"] = "generated_scene"
    duration_ms: int
    renderer: str
    spec: dict[str, Any] = Field(default_factory=dict)
    source_assets: list[str] = Field(default_factory=list)
    render_mode: str = ""


VideoElement = Annotated[
    Union[SourceCut, Clip, Background, ImageSequence, GeneratedScene],
    Field(discriminator="kind"),
]


# --------------------------------------------------------------------------- audio

class TimedAudioRef(_Element):
    kind: Literal["timed_audio"] = "timed_audio"
    asset: str
    duration_ms: int
    offset_ms: int = 0
    gain_db: float = 0.0
    role: Literal["narration", "recitation", "voice"] = "voice"


class Music(_Element):
    kind: Literal["music"] = "music"
    asset: str
    duration_ms: int
    gain_db: float = -18.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    loop: bool = True


class RoomTone(_Element):
    kind: Literal["room_tone"] = "room_tone"
    duration_ms: int
    generator: Literal["silence", "noise"] = "silence"
    gain_db: float = -30.0


AudioElement = Annotated[Union[TimedAudioRef, Music, RoomTone], Field(discriminator="kind")]


# --------------------------------------------------------------------------- captions

class WordSpan(BaseModel):
    model_config = _STRICT

    text: str
    start_ms: int
    end_ms: int


class Caption(BaseModel):
    model_config = _STRICT

    start_ms: int
    end_ms: int
    text: str
    lang: str = ""
    style_ref: str = ""
    words: list[WordSpan] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class Overlay(BaseModel):
    model_config = _STRICT

    start_ms: int
    end_ms: int
    text: str = ""
    asset: str = ""
    anchor: str = "bottom-center"
    style_ref: str = ""
    spec: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- timeline

class Timeline(BaseModel):
    model_config = _STRICT

    duration_ms: int
    aspect: Aspect = "vertical"
    render_mode: str
    style: str = ""
    video: list[VideoElement] = Field(default_factory=list)
    audio: list[AudioElement] = Field(default_factory=list)
    captions: list[Caption] = Field(default_factory=list)
    overlays: list[Overlay] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    # -- queries ---------------------------------------------------------------
    @property
    def unprepared(self) -> list[VideoElement]:
        return [el for el in self.video if not isinstance(el, Clip)]

    @property
    def prepared(self) -> bool:
        return not self.unprepared

    def video_at(self, start_ms: int) -> VideoElement | None:
        for el in self.video:
            end = el.start_ms + _duration_of(el)
            if el.start_ms <= start_ms < end:
                return el
        return None

    def materialise(self, replacements: dict[str, Clip]) -> "Timeline":
        """Return a copy with every declarative element replaced by its prepared clip."""
        missing = [el.element_id for el in self.unprepared if el.element_id not in replacements]
        if missing:
            raise ValueError(f"no prepared clip for elements: {', '.join(missing)}")
        video: list[VideoElement] = []
        for el in self.video:
            video.append(el if isinstance(el, Clip) else replacements[el.element_id])
        return self.model_copy(update={"video": video})


def _duration_of(element: VideoElement) -> int:
    if isinstance(element, Clip):
        return element.duration_ms
    if isinstance(element, SourceCut):
        return element.duration_ms
    if isinstance(element, GeneratedScene):
        return element.duration_ms
    if isinstance(element, ImageSequence):
        return element.duration_ms
    if isinstance(element, Background):
        return element.duration_ms
    return 0


# --------------------------------------------------------------------------- speech

class SpeechSegment(BaseModel):
    """One unit of speech (a line, an ayah, a narration beat) with its timing."""

    model_config = _STRICT

    text: str
    start_ms: int
    end_ms: int
    words: list[WordSpan] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class TimedSpeech(BaseModel):
    """What a narration/recitation provider returns: audio plus real timings."""

    model_config = _STRICT

    path: Path
    duration_ms: int
    segments: list[SpeechSegment] = Field(default_factory=list)
    voice: str = ""
    provenance: Provenance = Field(default_factory=Provenance)
    licence: Licence | None = None


def timeline_digest(timeline: Timeline) -> str:
    import hashlib

    payload = timeline.model_dump_json(exclude_none=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
