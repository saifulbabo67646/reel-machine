"""The HyperFrames renderer: one HTML project in, one MP4 (or one still) out.

HyperFrames seeks every frame of a composition in headless Chrome and encodes with
FFmpeg (https://github.com/heygen-com/hyperframes, Apache-2.0). It runs through `npx`
at a pinned version, with telemetry off and our own ffmpeg/ffprobe, in its own process
group so a cancelled job takes Chrome down with it.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..core.errors import ShotRenderFailed

HYPERFRAMES_VERSION = "0.8.146"
_ERRORS = re.compile(r"(\d+)\s+errors?")


def _node_major(node: str) -> int:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except Exception:  # noqa: BLE001 - an unreadable version counts as too old
        return 0


class HyperframesRenderer:
    name = "hyperframes"
    timeout_floor_s = 120

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ----------------------------------------------------------------- readiness
    def missing(self) -> list[str]:
        problems: list[str] = []
        node = shutil.which("node")
        if node is None:
            problems.append("node is not on PATH (HyperFrames needs Node.js >= 22)")
        elif _node_major(node) < 22:
            problems.append("Node.js >= 22 is required by HyperFrames")
        if shutil.which("npx") is None:
            problems.append("npx is not on PATH (it ships with Node.js)")
        if shutil.which(self.settings.ffmpeg) is None:
            problems.append(f"ffmpeg not found ({self.settings.ffmpeg!r})")
        if shutil.which(self.settings.ffprobe) is None:
            problems.append(
                f"ffprobe not found ({self.settings.ffprobe!r}) — HyperFrames will not run without it; "
                "install ffmpeg-full or point REEL_FFPROBE at an ffprobe binary"
            )
        return problems

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "renderers",
            "description": f"Three.js shots rendered by HyperFrames {HYPERFRAMES_VERSION} (Node + headless Chrome)",
            "missing": self.missing(),
        }

    # ------------------------------------------------------------------ running
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["DO_NOT_TRACK"] = "1"
        env["HYPERFRAMES_FFMPEG_PATH"] = shutil.which(self.settings.ffmpeg) or self.settings.ffmpeg
        env["HYPERFRAMES_FFPROBE_PATH"] = shutil.which(self.settings.ffprobe) or self.settings.ffprobe
        return env

    def _run(self, args: list[str], project: Path, *, timeout_s: float, cancel: Any = None) -> tuple[int, str]:
        npx = shutil.which("npx") or "npx"
        with tempfile.TemporaryFile(mode="w+") as log:
            proc = subprocess.Popen(
                [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", *args],
                cwd=project, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                env=self._env(), start_new_session=True, text=True,
            )
            if cancel is not None:
                cancel.register_process(proc)
            try:
                try:
                    code = proc.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                    log.seek(0)
                    raise ShotRenderFailed(
                        f"HyperFrames timed out after {timeout_s:.0f}s",
                        hint="the shot may be too heavy, or Chrome hung; re-run the job",
                        details={"project": str(project), "log": log.read()[-2000:]},
                    ) from None
            finally:
                if cancel is not None:
                    cancel.unregister_process(proc)
            if cancel is not None:
                cancel.raise_if_cancelled()
            log.seek(0)
            return code, log.read()

    def lint(self, project: Path, *, cancel: Any = None) -> tuple[bool, str]:
        code, out = self._run(["lint"], Path(project), timeout_s=120, cancel=cancel)
        match = _ERRORS.search(out)
        errors = int(match.group(1)) if match else (0 if code == 0 else 1)
        return errors == 0, out

    def snapshot(self, project: Path, at_s: float, dest: Path, *, cancel: Any = None) -> Path:
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(
                ["snapshot", "--at", f"{at_s:.3f}", "--no-end", "--output", tmp],
                Path(project), timeout_s=max(60, self.timeout_floor_s), cancel=cancel,
            )
            frames = sorted(Path(tmp).glob("frame-*.png"))
            if code != 0 or not frames:
                raise ShotRenderFailed(
                    "HyperFrames could not capture a still",
                    details={"project": str(project), "log": out[-2000:]},
                )
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(frames[0], dest)
        return dest

    def render(
        self, project: Path, dest: Path, *, fps: int = 30, seconds: float = 0.0, cancel: Any = None
    ) -> Path:
        project, dest = Path(project), Path(dest)
        ok, lint_out = self.lint(project, cancel=cancel)
        if not ok:
            raise ShotRenderFailed(
                "the shot did not pass HyperFrames lint",
                hint="this is a compiler bug in explainer3d; the lint output names the rule",
                details={"project": str(project), "lint": lint_out[-2000:]},
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        timeout_s = max(self.timeout_floor_s, 20 * seconds)
        code, out = self._run(
            ["render", "--output", str(dest), "--fps", str(fps), "--quality", "looks", "--strict"],
            project, timeout_s=timeout_s, cancel=cancel,
        )
        (project / "render.log").write_text(out, encoding="utf-8")
        if code != 0 or not dest.is_file() or dest.stat().st_size == 0:
            raise ShotRenderFailed(
                "HyperFrames did not produce the shot",
                details={"project": str(project), "log": out[-2000:]},
            )
        return dest
