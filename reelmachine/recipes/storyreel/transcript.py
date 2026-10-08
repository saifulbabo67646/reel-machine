"""The subtitle script, read the way an LLM can use it.

An SRT is parsed into cues, cues are grouped into scenes (the unit the model reasons
over and the clip plan references), and the whole thing can be written out as a
`[HH:MM:SS] line` transcript the calling agent can read directly.

Also here: the voiceover markers. `[Pause]`, `[Shock]`, `[Whisper]` and `[Suspense]`
steer delivery; they must never reach a TTS engine, and `[Pause]` becomes real silence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Cue, SceneChunk

#: A scene starts here when dialogue pauses this long; it is capped so a model prompt
#: never holds a whole act.
SCENE_GAP_MS = 1_800
SCENE_MIN_MS = 3_000
SCENE_MAX_MS = 60_000

_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")
_TIMECODE_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_SPEAKER_RE = re.compile(r"^\s*[-–—]\s*")

MARKER_NAMES = ("pause", "shock", "whisper", "suspense")
_MARKER_RE = re.compile(r"\[\s*(pause|shock|whisper|suspense)\s*\]", re.IGNORECASE)


def parse_timecode(value: str) -> int:
    """`HH:MM:SS,mmm` (hours optional, comma or dot) → milliseconds."""
    match = _TIMECODE_RE.search(value or "")
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    fraction = (match.group(4) or "0").ljust(3, "0")[:3]
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(fraction)


def strip_markup(text: str) -> str:
    """Drop HTML/ASS styling and collapse whitespace."""
    cleaned = _TAG_RE.sub("", text or "")
    cleaned = cleaned.replace("\\N", " ").replace("\\n", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_srt(text: str) -> list[Cue]:
    """Read an SRT into cues. Malformed blocks are skipped, duplicate lines merged."""
    cleaned = (text or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", cleaned):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        arrow = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if arrow is None:
            continue
        left, _, right = lines[arrow].partition("-->")
        start_ms = parse_timecode(left)
        end_ms = parse_timecode(right)
        body = " ".join(lines[arrow + 1 :])
        body = strip_markup(_SPEAKER_RE.sub("", body))
        if not body or end_ms <= start_ms:
            continue
        if cues and cues[-1].text == body and start_ms - cues[-1].end_ms <= 500:
            # SDH/overlap duplicates of the same line: one cue, the longer window
            cues[-1] = cues[-1].model_copy(update={"end_ms": max(cues[-1].end_ms, end_ms)})
            continue
        cues.append(Cue(index=len(cues) + 1, start_ms=start_ms, end_ms=end_ms, text=body))
    return cues


def segment_scenes(
    cues: list[Cue],
    *,
    gap_ms: int = SCENE_GAP_MS,
    min_ms: int = SCENE_MIN_MS,
    max_ms: int = SCENE_MAX_MS,
) -> list[SceneChunk]:
    """Group cues into scenes: split on long pauses and on length, never too short."""
    if not cues:
        return []
    groups: list[list[Cue]] = [[cues[0]]]
    for previous, cue in zip(cues, cues[1:]):
        group = groups[-1]
        long_pause = cue.start_ms - previous.end_ms >= gap_ms
        too_long = cue.end_ms - group[0].start_ms > max_ms
        if long_pause or too_long:
            groups.append([cue])
        else:
            group.append(cue)

    while len(groups) > 1:
        short = next(
            (index for index, group in enumerate(groups) if _group_ms(group) < min_ms),
            None,
        )
        if short is None:
            break
        if short + 1 < len(groups):
            groups[short + 1] = groups[short] + groups[short + 1]
        else:
            groups[short - 1] = groups[short - 1] + groups[short]
        del groups[short]

    scenes: list[SceneChunk] = []
    for index, group in enumerate(groups, start=1):
        scenes.append(
            SceneChunk(
                id=f"s{index:04d}",
                start_ms=group[0].start_ms,
                end_ms=max(cue.end_ms for cue in group),
                text=" ".join(cue.text for cue in group),
                cues=len(group),
            )
        )
    return scenes


def _group_ms(group: list[Cue]) -> int:
    return max(cue.end_ms for cue in group) - group[0].start_ms


def transcript_text(cues: list[Cue], *, title: str = "") -> str:
    """The readable transcript an agent (or a person) scans for viral moments."""
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
        lines.append("")
    for cue in cues:
        lines.append(f"[{format_ms(cue.start_ms)}] {cue.text}")
    return "\n".join(lines) + "\n"


def format_ms(ms: int) -> str:
    ms = max(0, int(ms))
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, _ = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def write_cues(cues: list[Cue], path) -> None:
    """Write cues back as a clean UTF-8 SRT (after offset/sync/normalisation)."""
    from pathlib import Path

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def stamp(ms: int) -> str:
        ms = max(0, int(ms))
        hours, remainder = divmod(ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    blocks = [
        f"{index}\n{stamp(cue.start_ms)} --> {stamp(cue.end_ms)}\n{cue.text}\n"
        for index, cue in enumerate(cues, start=1)
    ]
    dest.write_text("\n".join(blocks), encoding="utf-8")


def transcript_stats(cues: list[Cue], scenes: list[SceneChunk]) -> dict[str, object]:
    words = sum(len(cue.text.split()) for cue in cues)
    span_ms = max((cue.end_ms for cue in cues), default=0)
    return {
        "cues": len(cues),
        "scenes": len(scenes),
        "words": words,
        "spanMs": span_ms,
        "span": format_ms(span_ms),
    }


# ------------------------------------------------------------------ voice markers


def markers_in(text: str) -> list[str]:
    return [match.group(1).lower() for match in _MARKER_RE.finditer(text or "")]


def clean_voiceover(text: str) -> str:
    """The text a TTS engine should actually speak."""
    stripped = _MARKER_RE.sub(" ", text or "")
    return re.sub(r"\s+", " ", stripped).strip()


def speech_parts(text: str) -> list[str | None]:
    """Split into spoken chunks and pauses (`None`), in order.

    `"The door opened. [Pause] Nobody was there."` → two chunks with a pause between.
    """
    pieces = _MARKER_RE.split(text or "")
    parts: list[str | None] = []
    for index, piece in enumerate(pieces):
        if index % 2 == 1:
            if piece.lower() == "pause":
                parts.append(None)
            continue
        spoken = re.sub(r"\s+", " ", piece).strip()
        if spoken:
            parts.append(spoken)
    return parts


@dataclass(slots=True)
class CueWindow:
    """A run of cues overlapping a time window — what a clip plan is checked against."""

    start_ms: int
    end_ms: int
    text: str
    cues: int


def cues_in_window(cues: list[Cue], start_ms: int, end_ms: int) -> CueWindow:
    inside = [cue for cue in cues if cue.end_ms > start_ms and cue.start_ms < end_ms]
    return CueWindow(
        start_ms=start_ms,
        end_ms=end_ms,
        text=" ".join(cue.text for cue in inside),
        cues=len(inside),
    )
