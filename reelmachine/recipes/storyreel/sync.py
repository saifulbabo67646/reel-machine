"""Making a downloaded subtitle agree with the copy of the film we actually have.

Subtitles from a database were timed against *some* release; ours may differ by an
intro logo, a frame rate or an extended cut. Embedded subtitles need none of this —
they came out of the same file. For downloaded ones the rescue is, in order:

* `subtitle_offset_ms` — a caller who knows the shift says it outright;
* `ffsubsync` — when it is on PATH, align the subtitle to our audio track;
* otherwise the output records `synced: false` and verification warns, so a wrong
  clip plan is visible instead of mysterious.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .models import Cue

FFSUBSYNC_HINT = "install ffsubsync (e.g. `pipx install 'ffsubsync[auditok]'`) and re-run"


def apply_offset(cues: list[Cue], offset_ms: int) -> list[Cue]:
    """Shift every cue by `offset_ms`; drop what falls off the start of the film."""
    if not offset_ms:
        return cues
    shifted: list[Cue] = []
    for cue in cues:
        start = cue.start_ms + offset_ms
        end = cue.end_ms + offset_ms
        if end <= 0:
            continue
        shifted.append(
            cue.model_copy(update={"start_ms": max(0, start), "end_ms": max(1, end), "index": len(shifted) + 1})
        )
    return shifted


def sync_srt(video: Path, srt: Path, out: Path, *, timeout_s: float = 900.0) -> tuple[bool, str]:
    """Run ffsubsync on a subtitle. Returns `(synced, note)`; never raises."""
    binary = shutil.which("ffsubsync")
    if not binary:
        return False, f"ffsubsync is not on PATH — {FFSUBSYNC_HINT}"
    command = [binary, str(video), "-i", str(srt), "-o", str(out)]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"ffsubsync did not finish: {exc}"
    if proc.returncode != 0 or not out.is_file():
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-4:])
        return False, f"ffsubsync failed ({proc.returncode}): {tail or 'no output'}"
    return True, "synced with ffsubsync"
