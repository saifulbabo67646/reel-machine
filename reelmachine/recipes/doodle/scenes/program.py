"""Program mode: typed elements rendered deterministically with Pillow.

There is no generative step: the layout, the text and the relationships are computed from
the scene spec, so identical inputs produce identical frames. A Node/browser renderer
could be registered through the `reelmachine.renderers` entry point later; nothing here
requires one.
"""

from __future__ import annotations

import functools
import shutil
from pathlib import Path
from typing import Any

from .... import ffmpeg
from ....config import Settings
from ....core.errors import ProviderUnavailable
from ..models import SceneElement, SceneSpec

# A real face, so text can be measured; the fallback is Pillow's own.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
INSET = 26  # breathing room between a card's edge and its text


@functools.lru_cache(maxsize=64)
def _font(size: int):
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:  # pragma: no cover - unreadable font file
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - older Pillow
        return ImageFont.load_default()


def text_width(draw, text: str, font) -> float:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def wrap_to_width(draw, text: str, font, width: float) -> list[str]:
    """Greedy wrap by measured width — a word is never split down the middle."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and text_width(draw, candidate, font) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def fit_text(
    draw,
    text: str,
    *,
    width: float,
    height: float,
    max_size: int,
    min_size: int = 16,
    line_ratio: float = 1.3,
) -> tuple[Any, list[str]]:
    """Largest face size at which `text` wraps inside the box — measured, not guessed."""
    size = max(min_size, int(max_size))
    while size > min_size:
        font = _font(size)
        lines = wrap_to_width(draw, text, font, width)
        if len(lines) * size * line_ratio <= height:
            return font, lines
        size = int(size * 0.9)
    font = _font(min_size)
    lines = wrap_to_width(draw, text, font, width)
    room = max(1, int(height / (min_size * line_ratio)))
    if len(lines) > room:  # extremely long text: keep whole lines, mark the cut
        lines = lines[:room]
        lines[-1] = lines[-1].rstrip() + "…"
    return font, lines


def draw_block(
    draw,
    text: str,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    colour: tuple[int, int, int],
    max_size: int,
    inset: int = INSET,
    align: str = "left",
) -> None:
    """Draw `text` fitted and vertically centred inside its box, never past its edges."""
    box_w = max(24, width - inset * 2)
    box_h = max(20, height - inset * 2)
    font, lines = fit_text(draw, text, width=box_w, height=box_h, max_size=max_size)
    size = getattr(font, "size", None) or max_size
    line_h = size * 1.3
    top = y + inset + max(0.0, (box_h - len(lines) * line_h) / 2)
    for row, line in enumerate(lines):
        offset = 0 if align == "left" else max(0, (box_w - text_width(draw, line, font)) / 2)
        draw.text((x + inset + offset, top + row * line_h), line, fill=colour, font=font)


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
                draw_block(
                    draw,
                    element.text,
                    x=element.x,
                    y=element.y + offset,
                    width=element.width,
                    height=element.height,
                    colour=_mix(ink, (255, 255, 255), alpha),
                    max_size=max(20, int(element.height * 0.34)),
                )
        elif element.kind == "text":
            draw_block(
                draw,
                element.text,
                x=element.x,
                y=element.y + offset,
                width=element.width,
                height=element.height,
                colour=colour,
                max_size=max(24, int(element.height * 0.9)),
                inset=0,
            )
        elif element.kind == "label":
            width = int(element.width * (0.4 + 0.6 * progress))
            draw.rounded_rectangle(
                (element.x, element.y + offset, element.x + max(10, width), element.y + element.height + offset),
                radius=14,
                fill=_mix(accent, (255, 255, 255), alpha),
            )
            draw_block(
                draw,
                element.text,
                x=element.x,
                y=element.y + offset,
                width=element.width,
                height=element.height,
                colour=(255, 255, 255),
                max_size=max(18, int(element.height * 0.5)),
                inset=12,
                align="center",
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
