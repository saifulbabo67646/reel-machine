"""Background providers for the quranic recipe.

A background is either generated (a procedural gradient, or a solid colour for tests) or
fetched from a stock library (Pexels). Tenant-supplied files are first-class and need no
provider at all — they are the caller's own media, not a deployment dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ....core.assets import Licence, Provenance


class BackgroundRequest(BaseModel):
    """What the recipe asks a background provider for."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    query: str = ""
    media: str = "auto"
    allow_people: bool = False
    orientation: str = "portrait"
    min_height: int = 720
    colors: list[str] = Field(default_factory=list)
    speed: float = 0.02
    duration_ms: int = 0
    width: int = 1080
    height: int = 1920
    path: str = ""


@dataclass(slots=True)
class BackgroundClip:
    """A media file plus everything the manifest needs to explain where it came from."""

    path: Path
    provenance: Provenance = field(default_factory=Provenance)
    licence: Licence | None = None
    width: int = 0
    height: int = 0
    duration_ms: int = 0
    still: bool = False  # a photograph: the stage gives it the slow push-in
    face_check: str = ""  # "passed" | "blocked" | "skipped" | "unavailable"


@runtime_checkable
class BackgroundProvider(Protocol):
    name: str

    def missing(self) -> list[str]: ...

    def describe(self) -> dict: ...

    def fetch(self, request: BackgroundRequest, *, dest_dir: Path) -> BackgroundClip: ...
