"""Subtitles that are already inside the film we downloaded.

The best source when it is there: no key, no quota, no download, and its timestamps
were authored for exactly the file the micro-clips will come out of — so a clip plan
built on them lands where it says. Text codecs (`subrip`, `ass`, `mov_text`, …) convert
to SRT with ffmpeg; bitmap tracks (PGS, VobSub) are skipped, since they would need OCR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .... import ffmpeg
from ....config import get_settings
from . import SubtitleFetch, SubtitleRequest, describe_row, language_rank


class EmbeddedSubtitleProvider:
    name = "embedded"

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings or get_settings()

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "Text subtitles already inside the downloaded film (no key, exact sync)",
            [],
        )

    def fetch(self, request: SubtitleRequest, *, dest_dir: Path) -> SubtitleFetch | None:
        if not request.movie_path:
            return None
        source = Path(request.movie_path)
        if not source.is_file():
            return None
        try:
            info = ffmpeg.probe(source)
        except ffmpeg.FFmpegError:
            return None
        track = choose_track(info.text_subtitles(), request.languages)
        if track is None:
            return None

        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"embedded-{track.ordinal}.srt"
        command = [
            self.settings.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            f"0:s:{track.ordinal}",
            "-c:s",
            "srt",
            str(out),
        ]
        try:
            ffmpeg.run(command)
        except ffmpeg.FFmpegError:
            return None
        if not out.is_file() or out.stat().st_size == 0:
            return None
        return SubtitleFetch(
            path=str(out),
            language=track.language,
            source=self.name,
            meta={
                "track": {"ordinal": track.ordinal, "codec": track.codec, "language": track.language},
                "default": track.default,
                "forced": track.forced,
            },
        )


def choose_track(tracks: list[ffmpeg.SubtitleTrack], languages: list[str]) -> ffmpeg.SubtitleTrack | None:
    """The track to extract: the wanted language first, forced tracks only as a last resort.

    A forced track translates signs and alien dialogue, not the story, so it is never
    chosen while a full track exists.
    """
    if not tracks:
        return None
    ordered = sorted(
        tracks,
        key=lambda track: (
            language_rank(track.language, languages),
            1 if track.forced else 0,
            0 if track.default else 1,
            track.ordinal,
        ),
    )
    return ordered[0]
