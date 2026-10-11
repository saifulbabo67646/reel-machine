"""A deterministic clip source for tests and dry runs.

It builds one synthetic episode (through the existing `mock` source provider) and
writes the deterministic English story next to it as a sidecar `.srt` — then delegates
to the **real** `LibraryClipSource`. That is deliberate: a fake that reimplemented the
search would prove nothing about the search.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import Settings, get_settings
from ..storyreel.subtitles.fake import FAKE_STORY
from .clips import ClipCandidate, ClipRequest, describe_row
from .library import LibraryClipSource

#: The fake episode's synthetic id, and the name its sidecar takes.
MEDIA_ID = "WORDREELMOCK"
EPISODE = 1


class FakeClipSource:
    """One mock episode plus the deterministic story, scanned by the real library source."""

    name = "fake"
    version = "1"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._prepared = False

    # ------------------------------------------------------------------ provider
    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "A synthetic episode and a fixed English story, for tests and dry runs",
            [],
            directory=str(self.root),
        )

    @property
    def root(self) -> Path:
        return self.settings.workdir / "wordreel-fake"

    # --------------------------------------------------------------- delegation
    def find(self, request: ClipRequest, *, limit: int = 20) -> list[ClipCandidate]:
        self.prepare()
        return self._library().find(self._local(request), limit=limit)

    def suggest(self, request: ClipRequest, *, limit: int = 20) -> list[str]:
        self.prepare()
        return self._library().suggest(self._local(request), limit=limit)

    # ------------------------------------------------------------------ fixtures
    def prepare(self) -> Path:
        """Materialise the episode and its sidecar subtitle once, and return the video."""
        if self._prepared:
            return self.video
        from ...sources.mock import MockProvider

        self.root.mkdir(parents=True, exist_ok=True)
        asset = MockProvider(self.settings).resolve(None, EPISODE)
        source = Path(asset.local_path) if asset.local_path else self.video
        if source != self.video and source.is_file():
            self.video.write_bytes(source.read_bytes())
        if not self.video.is_file():  # pragma: no cover - mock provider contract
            raise RuntimeError("the mock provider produced no episode to cut from")

        duration_ms = max(60_000, int((asset.duration_ms or 0) or 150_000))
        self.subtitle.write_text(_story_srt(duration_ms), encoding="utf-8")
        self._prepared = True
        return self.video

    @property
    def video(self) -> Path:
        return self.root / f"{MEDIA_ID}_ep{EPISODE:03d}.mkv"

    @property
    def subtitle(self) -> Path:
        return self.root / f"{MEDIA_ID}_ep{EPISODE:03d}.en.srt"

    # ------------------------------------------------------------------ internals
    def _library(self) -> LibraryClipSource:
        return LibraryClipSource(self.settings)

    def _local(self, request: ClipRequest) -> ClipRequest:
        """The request, redirected at the fixture directory."""
        return request.model_copy(update={"library": [str(self.root)], "subtitles": []})


def _story_srt(duration_ms: int) -> str:
    """`FAKE_STORY` spread across the episode — the same shape the storyreel fake uses."""
    step = duration_ms / len(FAKE_STORY)
    blocks: list[str] = []
    for index, line in enumerate(FAKE_STORY, start=1):
        start = int((index - 1) * step + step * 0.1)
        end = int(start + step * 0.8)
        blocks.append(f"{index}\n{_stamp(start)} --> {_stamp(end)}\n{line}\n")
    return "\n".join(blocks)


def _stamp(ms: int) -> str:
    ms = max(0, int(ms))
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
