"""A stand-in renderer for tests: a moving pattern of the right size and length.

It honours the same contract as HyperFrames (project dir in, MP4 or PNG out, duration
from the root's data-duration) so the engine, compose and verify paths run without Node.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings, get_settings

_DURATION = re.compile(r'data-duration="([\d.]+)"')


def _duration(project: Path) -> float:
    return float(_DURATION.search((Path(project) / "index.html").read_text(encoding="utf-8")).group(1))


class FakeShotRenderer:
    name = "explainer3d-fake"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "group": "renderers", "description": "test pattern shots", "missing": []}

    def render(self, project: Path, dest: Path, *, fps: int = 30, seconds: float = 0.0, cancel: Any = None) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.run([
            self.settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate={fps}:duration={_duration(project):.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dest),
        ], cancel=cancel)
        return dest

    def snapshot(self, project: Path, at_s: float, dest: Path, *, cancel: Any = None) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.run([
            self.settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=1:duration=1",
            "-frames:v", "1", str(dest),
        ], cancel=cancel)
        return dest
