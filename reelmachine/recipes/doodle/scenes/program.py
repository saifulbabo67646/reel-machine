"""Program mode: typed elements rendered deterministically with Pillow.

There is no generative step: the layout, the text and the relationships are computed from
the scene spec, so identical inputs produce identical frames. A Node/browser renderer
could be registered through the `reelmachine.renderers` entry point later; nothing here
requires one.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .... import ffmpeg
from ....config import Settings
from ....core.errors import ProviderUnavailable
from ..models import SceneElement, SceneSpec


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - older Pillow
        return ImageFont.load_default()


def _mix(colour: tuple[int, int, int], background: tuple[int, int, int], alpha: float) -> tuple[int, int, int]:
    return tuple(int(round(background[i] + (colour[i] - background[i]) * alpha)) for i in range(3))


class ProgramRenderer:
    name = "program"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def missing(self) -> list[str]:
        try:
            import PIL  # noqa: F401
        except ImportError:
            return ["Pillow is required for program mode: pip install 'reel-machine[program]'"]
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "renderers",
            "description": "Deterministic knowledge graphics rendered with Pillow",
            "missing": self.missing(),
        }

    # -- frame authoring --------------------------------------------------------
    def _progress(self, element: SceneElement, time_ms: float) -> float:
        if time_ms < element.enter_ms:
            return 0.0
        if element.exit_ms and time_ms >= element.exit_ms:
            return 0.0
        span = max(1.0, min(600.0, element.exit_ms - element.enter_ms if element.exit_ms else 600.0))
        return max(0.0, min(1.0, (time_ms - element.enter_ms) / span))

    def frame(self, scene: SceneSpec, time_ms: float):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (scene.canvas_w, scene.canvas_h), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        for element in scene.elements:
            progress = self._progress(element, time_ms)
            if progress <= 0:
                continue
            self._draw_element(draw, scene, element, progress)
        return image

    def _draw_element(self, draw, scene: SceneSpec, element: SceneElement, progress: float) -> None:
        ink = (27, 27, 27)
        accent = (217, 79, 48)
        offset = 0
        if element.enter == "slide":
            offset = int((1 - progress) * -40)
        alpha = 1.0 if element.enter == "draw" else progress
        colour = _mix(ink, (255, 255, 255), alpha)
        if element.kind == "card":
            width = element.width if element.enter != "draw" else int(element.width * progress)
            draw.rounded_rectangle(
                (element.x, element.y + offset, element.x + max(8, width), element.y + element.height + offset),
                radius=24,
                outline=colour,
                width=4,
            )
            if progress > 0.5:
                draw.multiline_text(
                    (element.x + 24, element.y + 24 + offset),
                    element.text,
                    fill=_mix(ink, (255, 255, 255), alpha),
                    font=_font(max(20, int(element.height * 0.24))),
                )
        elif element.kind == "text":
            draw.text(
                (element.x, element.y + offset),
                element.text,
                fill=colour,
                font=_font(max(24, int(element.height * 0.7))),
            )
        elif element.kind == "label":
            width = int(element.width * (0.4 + 0.6 * progress))
            draw.rounded_rectangle(
                (element.x, element.y + offset, element.x + max(10, width), element.y + element.height + offset),
                radius=14,
                fill=_mix(accent, (255, 255, 255), alpha),
            )
            draw.text(
                (element.x + 12, element.y + 10 + offset),
                element.text,
                fill=(255, 255, 255),
                font=_font(max(18, int(element.height * 0.45))),
            )
        elif element.kind == "arrow" and len(element.points) >= 2:
            (x0, y0), (x1, y1) = element.points[0], element.points[-1]
            tip_y = int(y0 + (y1 - y0) * progress)
            draw.line((x0, y0, x1, tip_y), fill=colour, width=5)
            if progress > 0.85:
                draw.polygon(
                    [(x1, y1), (x1 - 14, y1 - 20), (x1 + 14, y1 - 20)],
                    fill=colour,
                )
        elif element.kind == "figure":
            radius = int(min(element.width, element.height) * 0.4 * progress)
            centre_x = element.x + element.width // 2
            centre_y = element.y + element.height // 2
            draw.ellipse(
                (centre_x - radius, centre_y - radius, centre_x + radius, centre_y + radius),
                outline=colour,
                width=4,
            )

    # -- rendering --------------------------------------------------------------
    def render(
        self,
        scene: SceneSpec,
        *,
        dest: Path,
        settings: Settings,
        fps: int = 30,
        cancel: Any = None,
    ) -> Path:
        problems = self.missing()
        if problems:
            raise ProviderUnavailable(
                "the program renderer is not installed",
                hint="; ".join(problems),
                details={"mode": "program"},
            )
        frames_dir = dest.parent / f"frames-{scene.id}"
        shutil.rmtree(frames_dir, ignore_errors=True)
        frames_dir.mkdir(parents=True, exist_ok=True)
        total_frames = max(1, int(round(scene.duration_ms * fps / 1000)))
        for index in range(total_frames):
            if cancel is not None:
                cancel.raise_if_cancelled()
            self.frame(scene, index * 1000.0 / fps).save(frames_dir / f"f-{index:05d}.png")

        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-framerate", str(fps),
            "-i", str(frames_dir / "f-%05d.png"),
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", "-r", str(fps), str(dest),
        ]
        ffmpeg.run(cmd, cancel=cancel)
        shutil.rmtree(frames_dir, ignore_errors=True)
        return dest
