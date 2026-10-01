"""Deterministic scene specs: structure, layout and the reveal schedule.

Nothing here is generative. Titles, numbers and relationships are computed from the
beat's text; the reveal schedule is read from the narration's real word timings, never
from even division.
"""

from __future__ import annotations

import re
from typing import Sequence

from ....core.timeline import WordSpan
from ..models import (
    Beat,
    DoodleInputs,
    Region,
    SceneElement,
    SceneRegion,
    SceneSpec,
)

GEOMETRY = {"vertical": (1080, 1920), "landscape": (1920, 1080)}


def choose_structure(inputs: DoodleInputs, beats: Sequence[Beat]) -> str:
    """`auto` prefers multi-act for 9:16 — a single tall canvas lets stories collide."""
    if inputs.scene_structure != "auto":
        return inputs.scene_structure
    if len(beats) <= 1:
        return "single"
    return "multi_act" if inputs.aspect == "vertical" else "single"


def split_words(words: Sequence[WordSpan], ratio: float) -> tuple[list[WordSpan], list[WordSpan]]:
    """Split a beat's words at `ratio`, by count — the seam between two semantic islands."""
    if not words:
        return [], []
    cut = max(1, min(len(words) - 1, int(round(len(words) * ratio)))) if len(words) > 1 else 1
    return list(words[:cut]), list(words[cut:])


def local_words(words: Sequence[WordSpan], offset_ms: int) -> list[WordSpan]:
    """Scene-local copies of a beat's words (a scene's clock starts at zero)."""
    return [
        WordSpan(
            text=word.text,
            start_ms=word.start_ms - offset_ms,
            end_ms=word.end_ms - offset_ms,
        )
        for word in words
    ]


def stroke_scene(
    beat: Beat,
    index: int,
    *,
    structure: str,
    words: Sequence[WordSpan],
    duration_ms: int,
    canvas: tuple[int, int],
) -> SceneSpec:
    """One stroke scene, on its own clock.

    `single` and `multi_act` are one whole-canvas scene each — multi-act means several
    scenes in sequence (a single tall canvas lets a long story collide with itself).
    `dual_islands` splits one scene into two semantic islands, and the seam between them
    is read from the narration's word timings.
    """
    width, height = canvas
    duration = max(400, duration_ms)
    # The ink must finish ~500 ms before the window ends so the runtime's own gaze tail
    # (at least half a second of the finished picture) fits inside the beat's narration.
    fill_deadline = max(200, duration - 500)
    regions: list[SceneRegion] = []
    if structure in ("single", "multi_act"):
        ink_end = min(fill_deadline, words[-1].end_ms if words else fill_deadline)
        regions.append(
            SceneRegion(
                id=f"{beat.id}-all",
                label=beat.id,
                sequence=1,
                region=Region(x=0, y=0, width=width, height=height),
                start_ms=0,
                duration_ms=max(200, ink_end),
            )
        )
    elif structure == "dual_islands":
        gutter = int(width * 0.06)
        half = (width - gutter * 3) // 2
        left_words, right_words = split_words(words, 0.5)
        left_start = left_words[0].start_ms if left_words else 0
        left_end = left_words[-1].end_ms if left_words else duration // 2
        right_start = right_words[0].start_ms if right_words else left_end
        right_end = right_words[-1].end_ms if right_words else duration
        left_start = max(0, min(left_start, duration - 300))
        right_start = max(left_start + 200, min(right_start, fill_deadline - 200))
        regions.append(
            SceneRegion(
                id=f"{beat.id}-left",
                label=f"{beat.id} (left)",
                sequence=1,
                region=Region(x=gutter, y=0, width=half, height=height),
                start_ms=left_start,
                duration_ms=max(200, min(left_end, fill_deadline) - left_start),
            )
        )
        regions.append(
            SceneRegion(
                id=f"{beat.id}-right",
                label=f"{beat.id} (right)",
                sequence=2,
                region=Region(x=gutter * 2 + half, y=0, width=half, height=height),
                start_ms=right_start,
                duration_ms=max(200, min(right_end, fill_deadline) - right_start),
                protected=[regions[0].region],
            )
        )
    else:  # pragma: no cover - the literal set is closed
        raise ValueError(f"unknown scene structure {structure!r}")

    return SceneSpec(
        id=beat.id,
        index=index,
        mode="stroke",
        structure=structure,
        narration=beat.narration,
        duration_ms=duration,
        canvas_w=width,
        canvas_h=height,
        regions=regions,
        meta={"keywords": list(beat.keywords)},
    )


def program_scene(
    beat: Beat,
    index: int,
    *,
    canvas: tuple[int, int],
    duration_ms: int,
    words: Sequence[WordSpan] = (),
) -> SceneSpec:
    """Knowledge graphics: cards, an arrow relationship and precise text, all computed."""
    width, height = canvas
    duration = max(600, duration_ms)
    last_word_end = words[-1].end_ms if words else duration
    if last_word_end < 200:
        last_word_end = duration
    title = (beat.keywords[0] if beat.keywords else beat.narration.split(" ")[0][:24]).strip()
    # one sentence per card; the renderer measures and wraps the text inside it
    body = [
        line.strip()
        for line in re.split(r"(?<=[.!?])\s+", beat.narration.strip())
        if line.strip()
    ][:2] or [beat.narration.strip()]
    margin = int(width * 0.08)
    card_w = width - margin * 2
    card_h = int(height * 0.16) if height > width else int(height * 0.24)
    top = int(height * 0.16)

    # entries are anchored to the narration's word timings, not to even division
    def word_at(fraction: float) -> int:
        if not words:
            return int(duration * fraction)
        index = min(len(words) - 1, max(0, int(len(words) * fraction)))
        return int(words[index].start_ms)

    elements: list[SceneElement] = [
        SceneElement(
            id=f"{beat.id}-title",
            kind="text",
            text=title,
            x=margin,
            y=int(height * 0.07),
            width=card_w,
            height=int(height * 0.07),
            enter_ms=word_at(0.0),
            exit_ms=duration,
            enter="fade",
            style_ref="doodle.title",
        )
    ]
    for row, line in enumerate(body):
        elements.append(
            SceneElement(
                id=f"{beat.id}-card{row + 1}",
                kind="card",
                text=line,
                x=margin,
                y=top + row * int(card_h * 1.25),
                width=card_w,
                height=card_h,
                enter_ms=word_at(0.15 + 0.3 * row),
                exit_ms=duration,
                enter="draw",
                style_ref="doodle.card",
            )
        )
    if len(body) >= 2:
        first, second = elements[1], elements[2]
        arrow_x = first.x + first.width // 2
        elements.append(
            SceneElement(
                id=f"{beat.id}-arrow",
                kind="arrow",
                text="",
                x=arrow_x,
                y=first.y + first.height,
                width=0,
                height=max(1, second.y - (first.y + first.height)),
                enter_ms=max(first.enter_ms, word_at(0.65)),
                exit_ms=duration,
                enter="draw",
                style_ref="doodle.arrow",
                points=[
                    (arrow_x, first.y + first.height + 4),
                    (arrow_x, second.y - 6),
                ],
            )
        )
    for column, keyword in enumerate(beat.keywords[1:4]):
        elements.append(
            SceneElement(
                id=f"{beat.id}-label{column + 1}",
                kind="label",
                text=keyword,
                x=margin + column * int(card_w / 3),
                y=top + len(body) * int(card_h * 1.25) + 40,
                width=int(card_w / 3) - 16,
                height=int(card_h * 0.5),
                enter_ms=word_at(0.8 + 0.1 * column),
                exit_ms=duration,
                enter="fade",
                style_ref="doodle.label",
            )
        )
    return SceneSpec(
        id=beat.id,
        index=index,
        mode="program",
        structure="single",
        narration=beat.narration,
        duration_ms=duration,
        canvas_w=width,
        canvas_h=height,
        elements=elements,
        meta={"keywords": list(beat.keywords)},
    )
