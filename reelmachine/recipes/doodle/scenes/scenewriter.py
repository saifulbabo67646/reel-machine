"""Scene writers: a beat becomes raster line art.

Supplied art is used exactly as given; the deterministic sketch writer draws neutral
line art from the beat's keywords so a run without supplied art is still possible. A
deployment may register any other writer the same way as every other provider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import Beat
from . import raster


@runtime_checkable
class SceneWriter(Protocol):
    name: str

    def missing(self) -> list[str]: ...

    def draw(self, beat: Beat, *, canvas: tuple[int, int], index: int, dest_dir: Path) -> Path: ...


class SketchSceneWriter:
    """Neutral, deterministic line art: no third-party visual IP in core."""

    name = "sketch"

    def missing(self) -> list[str]:
        try:
            import PIL  # noqa: F401
        except ImportError:
            return ["Pillow is required to draw line art: pip install 'reel-machine[stroke]'"]
        return []

    def draw(self, beat: Beat, *, canvas: tuple[int, int], index: int, dest_dir: Path) -> Path:
        keyword = (beat.keywords[0] if beat.keywords else beat.id) or "idea"
        path = dest_dir / f"{beat.id or f'scene-{index}'}.png"
        return raster.sketch(path, size=canvas, keyword=keyword, seed=index)
