"""Scene widening: turning one line into a cuttable scene.

The case that motivated this: `やっぱり... やっぱり!` in Rent-a-Girlfriend ep 7 is
2.4 s of audio.  Cut on its own it is a confusing fragment; with the two
neighbouring lines the `/context` endpoint returns, it becomes a 23.8 s
exchange — which is then clamped to something reel-sized.
"""

from __future__ import annotations

import pytest

from reelmachine.models import Segment, TextJa, Translation
from reelmachine.recipes.nadeshiko_cut.selection import compute_scene_window


def seg(public_id: str, start: int, end: int, text: str = "…") -> Segment:
    return Segment(
        publicId=public_id,
        position=0,
        status="ACTIVE",
        startTimeMs=start,
        endTimeMs=end,
        contentRating="SAFE",
        episode=7,
        mediaPublicId="m-GMT2prhVyw",
        textJa=TextJa(content=text),
        textEn=Translation(content="…"),
        textEs=Translation(content="…"),
        urls={"imageUrl": "i", "audioUrl": "a", "videoUrl": "v"},
    )


# The real data for the やっぱり case, straight from the API.
WORD_START, WORD_END = 530_980, 533_420
NEIGHBOURS = [
    seg("before1", 515_570, 518_390, "スマホ いじってる状況かよ!"),
    seg("after1", 536_100, 539_320, "よかった 行ったかー"),
]


def test_word_alone_is_too_short_to_cut() -> None:
    low, high = compute_scene_window(WORD_START, WORD_END, [], max_ms=14_000, min_ms=3_000)
    assert high - low == 3_000  # padded up to the floor, still only 3 s
    assert low <= WORD_START and WORD_END <= high


def test_context_widens_the_window_to_the_exchange() -> None:
    """Without a cap the window would span the neighbouring lines."""
    low, high = compute_scene_window(WORD_START, WORD_END, NEIGHBOURS, max_ms=0, min_ms=3_000)
    assert (low, high) == (515_570, 539_320)
    assert high - low == 23_750


def test_cap_centres_the_window_on_the_word() -> None:
    low, high = compute_scene_window(WORD_START, WORD_END, NEIGHBOURS, max_ms=14_000, min_ms=3_000)
    assert high - low == 14_000
    # the word sits inside, roughly central
    assert low <= WORD_START < WORD_END <= high
    centre = (WORD_START + WORD_END) // 2
    assert abs(((low + high) // 2) - centre) <= 500


def test_word_is_never_pushed_outside_the_window() -> None:
    """A very late word with far-away neighbours must still be contained."""
    low, high = compute_scene_window(
        600_000, 601_000, [seg("far", 100_000, 101_000)], max_ms=10_000, min_ms=3_000
    )
    assert low <= 600_000 and 601_000 <= high
    assert high - low == 10_000


def test_window_never_goes_negative() -> None:
    low, high = compute_scene_window(500, 900, [], max_ms=10_000, min_ms=5_000)
    assert low == 0
    assert high == 5_000


def test_short_neighbours_still_grow_to_the_floor() -> None:
    low, high = compute_scene_window(
        10_000, 10_400, [seg("a", 9_500, 9_900), seg("b", 10_500, 10_800)],
        max_ms=14_000, min_ms=4_000,
    )
    assert high - low == 4_000
    assert low <= 10_000 and 10_400 <= high


def test_single_neighbour_on_one_side_only() -> None:
    low, high = compute_scene_window(WORD_START, WORD_END, [NEIGHBOURS[0]], max_ms=0, min_ms=1_000)
    assert low == 515_570 and high == 533_420


@pytest.mark.parametrize("max_ms,min_ms", [(14_000, 3_000), (8_000, 2_000), (20_000, 5_000)])
def test_invariants_hold_for_any_settings(max_ms: int, min_ms: int) -> None:
    low, high = compute_scene_window(WORD_START, WORD_END, NEIGHBOURS, max_ms=max_ms, min_ms=min_ms)
    assert low >= 0
    assert low < high
    assert low <= WORD_START and WORD_END <= high
    assert high - low <= max(max_ms, min_ms)
