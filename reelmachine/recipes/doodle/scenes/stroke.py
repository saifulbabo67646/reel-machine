"""Stroke mode: turn a SceneSpec into the vendored runtime's annotation and render it.

The runtime (vendored, MIT — see `reelmachine/vendor/srt-whiteboard-animation/`) is
invoked as a subprocess. The annotation schema is validated by our own pydantic model
before a render is attempted, so a bad region fails in milliseconds instead of minutes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ....config import Settings
from ....core.errors import PreflightFailed, ProviderUnavailable
from ..models import Region, SceneSpec

VENDOR_ROOT = Path(__file__).resolve().parents[3] / "vendor" / "srt-whiteboard-animation"
RENDER_SCRIPT = VENDOR_ROOT / "scripts" / "render_stream_whiteboard.py"
HAND_PNG = VENDOR_ROOT / "assets" / "drawing-hand.png"


class AnnotationRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    width: int
    height: int


class AnnotationReveal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    startMs: int
    durationMs: int
    protectedRegions: list[AnnotationRegion] = Field(default_factory=list)


class AnnotationElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    sequence: int = 1
    region: AnnotationRegion
    reveal: AnnotationReveal

    @model_validator(mode="after")
    def _non_empty(self) -> "AnnotationElement":
        if self.region.width <= 0 or self.region.height <= 0:
            raise ValueError("a region must have a positive size")
        if self.reveal.durationMs <= 0:
            raise ValueError("a reveal must have a positive duration")
        return self


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canvas: dict[str, int]
    sceneDurationMs: int
    elements: list[AnnotationElement]

    @model_validator(mode="after")
    def _inside_canvas(self) -> "Annotation":
        width, height = self.canvas.get("width", 0), self.canvas.get("height", 0)
        if width <= 0 or height <= 0:
            raise ValueError("the canvas must have a positive size")
        for element in self.elements:
            rect = element.region
            if rect.x < 0 or rect.y < 0 or rect.x + rect.width > width or rect.y + rect.height > height:
                raise ValueError(f"region {element.label!r} leaves the canvas")
        return self


def build_annotation(scene: SceneSpec, *, art_size: tuple[int, int]) -> Annotation:
    """Scale the spec's canvas onto the actual line art and validate the result."""
    art_w, art_h = art_size
    factor_x = art_w / max(1, scene.canvas_w)
    factor_y = art_h / max(1, scene.canvas_h)
    elements: list[AnnotationElement] = []
    for region in scene.regions:
        scaled = region.region.scaled(min(factor_x, factor_y))
        protected = [
            AnnotationRegion(**rect.scaled(min(factor_x, factor_y)).model_dump())
            for rect in region.protected
        ]
        elements.append(
            AnnotationElement(
                label=region.label or region.id,
                sequence=region.sequence,
                region=AnnotationRegion(**scaled.model_dump()),
                reveal=AnnotationReveal(
                    startMs=max(0, region.start_ms),
                    durationMs=max(1, region.duration_ms),
                    protectedRegions=protected,
                ),
            )
        )
    return Annotation(
        canvas={"width": art_w, "height": art_h},
        sceneDurationMs=max(scene.duration_ms, max((e.reveal.startMs + e.reveal.durationMs for e in elements), default=0) + 400),
        elements=elements,
    )


class StrokeRenderer:
    name = "stroke"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def missing(self) -> list[str]:
        problems: list[str] = []
        if not RENDER_SCRIPT.is_file():
            problems.append("the vendored stroke runtime is missing from this install")
        for module, package in (("cv2", "opencv-python-headless"), ("av", "av"), ("PIL", "Pillow")):
            try:
                __import__(module)
            except ImportError:
                problems.append(f"{package} is not installed (pip install 'reel-machine[stroke]')")
        return problems

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "renderers",
            "description": "Whiteboard inking over line art, no browser and no GPU",
            "missing": self.missing(),
        }

    def render(
        self,
        scene: SceneSpec,
        *,
        art: Path,
        dest: Path,
        ink_path: str = "skeleton",
        color_fill: str = "contour-wipe",
        hand_follow: str = "",
        fps: int = 24,
        total_ms: int | None = None,
        cancel: Any = None,
    ) -> Path:
        from PIL import Image

        problems = self.missing()
        if problems:
            raise ProviderUnavailable(
                "the stroke renderer is not installed",
                hint="; ".join(problems),
                details={"mode": "stroke"},
            )
        with Image.open(art) as image:
            art_size = image.size
        annotation = build_annotation(scene, art_size=art_size)
        dest.parent.mkdir(parents=True, exist_ok=True)
        annotation_path = dest.with_suffix(".annotation.json")
        annotation_path.write_text(
            json.dumps(annotation.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        cmd = [
            sys.executable,
            str(RENDER_SCRIPT),
            str(art),
            str(annotation_path),
            str(dest),
            str(HAND_PNG),
            "--ink-path", ink_path,
            "--color-fill", color_fill,
            "--fps", str(fps),
        ]
        if total_ms:
            cmd += ["--total-ms", str(int(total_ms))]
        if cancel is not None:
            cancel.raise_if_cancelled()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not dest.is_file():
            raise PreflightFailed(
                "the stroke renderer did not produce a scene track",
                hint="see the runtime output for the failing region",
                details={
                    "scene": scene.id,
                    "returncode": proc.returncode,
                    "stderr": (proc.stderr or "")[-400:],
                    "stdout": (proc.stdout or "")[-400:],
                },
            )
        return dest

    def hand_asset(self) -> Path | None:
        return HAND_PNG if HAND_PNG.is_file() else None


def vendored_script() -> Path:
    return RENDER_SCRIPT


def vendor_available() -> bool:
    return RENDER_SCRIPT.is_file() and shutil.which(sys.executable) is not None


__all__ = [
    "Annotation",
    "AnnotationElement",
    "AnnotationRegion",
    "AnnotationReveal",
    "StrokeRenderer",
    "build_annotation",
    "vendor_available",
    "vendored_script",
    "Region",
]
