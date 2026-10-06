"""Candidate scoring, corpus caps and scene windows.

Moved verbatim from the previous single-pipeline module: the maths is load-bearing and
was already correct.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ...config import Settings
from ...models import Media, Segment

IDEAL_MIN_MS = 1200
IDEAL_MAX_MS = 9000

#: How much material to line up for a `target_ms` reel.  A tight cut is short
#: and a clip or two may fail to align, so selecting exactly the target tends
#: to land under it; a fifth again usually absorbs the losses.
TARGET_OVERSHOOT = 1.2


def compute_scene_window(
    word_start_ms: int,
    word_end_ms: int,
    context: Iterable[Segment] = (),
    *,
    max_ms: int = 14_000,
    min_ms: int = 3_000,
) -> tuple[int, int]:
    """The window to cut, in source time, including the surrounding dialogue.

    A single segment is often under a second — `やっぱり!` alone teaches a viewer
    nothing about how the word is used.  `Segment.startTimeMs` only ever
    describes one line, so the scene has to be widened with
    `GET /v1/media/segments/{id}/context`, which returns the neighbouring
    segments of the same episode with their own exact times.

    The window spans those neighbours and is then clamped: centred on the word
    when the exchange is longer than `max_ms`, padded out when it is shorter than
    `min_ms`.  The word always stays inside the result.
    """
    neighbours = list(context)
    low = min([word_start_ms, *(s.startTimeMs for s in neighbours)])
    high = max([word_end_ms, *(s.endTimeMs for s in neighbours)])

    if max_ms > 0 and high - low > max_ms:
        centre = (word_start_ms + word_end_ms) // 2
        low = max(low, centre - max_ms // 2)
        high = min(high, low + max_ms)
        low = max(0, high - max_ms)
    elif min_ms > 0 and high - low < min_ms:
        deficit = min_ms - (high - low)
        low = max(0, low - deficit // 2)
        high = low + min_ms
    return low, high


def compute_cut_window(
    word_start_ms: int,
    word_end_ms: int,
    *,
    pad_ms: int = 220,
    min_ms: int = 0,
    max_ms: int = 14_000,
) -> tuple[int, int]:
    """The window to actually *cut*, which is narrower than the scene.

    A scene is widened with neighbouring lines so the alignment has something
    to lock onto, but the caption only ever describes the one line the word is
    in.  Cutting the whole scene therefore shows several seconds of video with
    a caption that has already gone — the word is over before the clip is.

    Cutting just the line, padded by `pad_ms` on each side, keeps picture and
    caption together.  The cost is that clips get short, which is why the
    planner keeps adding them until the reel reaches `target_ms`.
    """
    low = max(0, word_start_ms - max(0, pad_ms))
    high = word_end_ms + max(0, pad_ms)
    if high <= low:
        return low, high
    if min_ms and high - low < min_ms:
        deficit = min_ms - (high - low)
        low = max(0, low - deficit // 2)
        high = low + min_ms
    if max_ms and high - low > max_ms:
        centre = (word_start_ms + word_end_ms) // 2
        low = max(0, centre - max_ms // 2)
        high = low + max_ms
    return low, high


def select_segments(
    usable: Sequence[Segment],
    media_by_id: dict[str, Media],
    settings: Settings,
    *,
    max_segments: int,
    min_segments: int = 1,
    per_media: int = 1,
    per_category: int | None = None,
) -> list[Segment]:
    """Pick the clips a reel is built from, best-scoring first.

    Three limits apply at once, and each exists for a different reason:

    * `per_media` keeps one title from filling the reel;
    * `per_category` does the same for a whole corpus — anime outnumbers
      J-Drama in the index by roughly 15 to 1, so without it a "mixed" reel is
      simply an anime reel with the occasional drama line;
    * `max_segments` is a hard ceiling, while `settings.target_ms` is the goal:
      a tight cut is only a few seconds, so the count is driven by how much
      running time has been lined up rather than by a fixed number.

    The overshoot on the target leaves room for a clip or two failing to align
    without the finished reel dropping under it.
    """
    ranked = sorted(usable, key=score_segment, reverse=True)
    cap = settings.per_category if per_category is None else per_category
    goal_ms = int(settings.target_ms * TARGET_OVERSHOOT) if settings.target_ms > 0 else 0
    per_media_count: dict[str, int] = {}
    per_category_count: dict[str, int] = {}
    selected: list[Segment] = []
    planned_ms = 0
    for segment in ranked:
        if per_media_count.get(segment.mediaPublicId, 0) >= per_media:
            continue
        kind = _category_of(segment, media_by_id)
        if cap and per_category_count.get(kind, 0) >= cap:
            continue
        per_media_count[segment.mediaPublicId] = per_media_count.get(segment.mediaPublicId, 0) + 1
        per_category_count[kind] = per_category_count.get(kind, 0) + 1
        selected.append(segment)
        planned_ms += expected_clip_ms(segment, settings)
        if len(selected) >= max_segments:
            break
        if goal_ms and len(selected) >= min_segments and planned_ms >= goal_ms:
            break
    return selected


def _category_of(segment: Segment, media_by_id: dict[str, Media]) -> str:
    """`ANIME` / `JDRAMA` / … for a segment, from the media it belongs to."""
    media = media_by_id.get(segment.mediaPublicId)
    return getattr(media, "category", "") or "ANIME"


def expected_clip_ms(segment: Segment, settings: Settings) -> int:
    """How long this candidate's clip will be, before any alignment is spent.

    The planner needs this to decide how many clips a reel needs, and the
    answer is available from the search result alone: the line's own duration
    plus the padding and roll that will be added around it.
    """
    pad = max(0, settings.cut_pad_ms) if settings.cut_mode == "line" else 0
    base = segment.duration_ms + 2 * pad
    if settings.cut_mode != "line":
        base = max(base, settings.min_clip_ms)
    return base + max(0, settings.pre_roll_ms) + max(0, settings.post_roll_ms)


def score_segment(segment: Segment) -> float:
    """Rank candidates: prefer human translations, SAFE, and comfortable length."""
    score = 0.0
    score += 3.0 if not segment.textEn.isMachineTranslated else 0.0
    score += 2.0 if segment.contentRating == "SAFE" else 0.0
    duration = segment.duration_ms
    if IDEAL_MIN_MS <= duration <= IDEAL_MAX_MS:
        score += 2.0
    elif duration < IDEAL_MIN_MS:
        score -= 1.5
    elif duration > 20000:
        score -= 2.5
    elif duration > IDEAL_MAX_MS:
        score -= 0.5
    score += min(len(segment.japanese), 40) / 40.0
    if segment.textJa.tokens:
        score += 0.5  # token data means we can highlight the word precisely
    return score
