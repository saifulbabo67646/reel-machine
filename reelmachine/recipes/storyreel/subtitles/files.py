"""Whatever a subtitle database hands back, normalised to UTF-8 SRT.

Downloads arrive as zips, ASS/SSA or Windows-encoded SRT; the pipeline downstream only
ever wants UTF-8 SRT.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from .... import ffmpeg
from ....config import get_settings

TEXT_SUFFIXES = (".srt", ".ass", ".ssa", ".vtt", ".sub")


def read_text(path: Path) -> str | None:
    """Read a subtitle file in whichever common encoding it uses."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def to_srt(downloaded: Path, dest_dir: Path, *, cancel: object = None) -> Path | None:
    """Normalise a downloaded file to UTF-8 SRT; `None` when nothing usable is inside."""
    path = Path(downloaded)
    if path.suffix.lower() == ".zip":
        extracted = _extract_zip(path, dest_dir)
        if extracted is None:
            return None
        path = extracted
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "subtitle.srt"
    suffix = path.suffix.lower()
    if suffix == ".srt":
        text = read_text(path)
        if not text or "-->" not in text:
            return None
        dest.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
        return dest
    if suffix in (".ass", ".ssa", ".vtt"):
        settings = get_settings()
        command = [
            settings.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(path),
            "-map",
            "0:s:0",
            "-c:s",
            "srt",
            str(dest),
        ]
        try:
            ffmpeg.run(command, cancel=cancel)
        except ffmpeg.FFmpegError:
            return None
        return dest if dest.is_file() and dest.stat().st_size else None
    return None


def _extract_zip(archive: Path, dest_dir: Path) -> Path | None:
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = [
                info
                for info in bundle.infolist()
                if not info.is_dir() and Path(info.filename).suffix.lower() in TEXT_SUFFIXES
            ]
            if not members:
                return None
            ranked = sorted(
                members,
                key=lambda info: (
                    TEXT_SUFFIXES.index(Path(info.filename).suffix.lower()),
                    -info.file_size,
                ),
            )
            chosen = ranked[0]
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / Path(chosen.filename).name
            with bundle.open(chosen) as source, target.open("wb") as sink:
                sink.write(source.read())
            return target if target.stat().st_size else None
    except (zipfile.BadZipFile, OSError):
        return None
