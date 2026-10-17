"""Segment kinds: a rank badge that follows the fill, a stat card that pops when its value is said."""

from pathlib import Path

import pytest

from reelmachine.core.timeline import WordSpan
from reelmachine.recipes.mapreel import compose, kinds
from reelmachine.recipes.mapreel.geometry import shape_of
from reelmachine.recipes.mapreel.models import MapReelInputs, ResolvedSegment
from reelmachine.recipes.mapreel.places import Atlas

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"


@pytest.fixture(scope="module")
def atlas():
    return Atlas.load(FIXTURE)


@pytest.mark.parametrize("value, spec", [
    ("70%", {"prefix": "", "number": 70.0, "decimals": 0, "comma": False, "suffix": "%"}),
    ("$1,200.50 bn", {"prefix": "$", "number": 1200.5, "decimals": 2, "comma": True, "suffix": " bn"}),
    ("No. 1", {"prefix": "No. ", "number": 1.0, "decimals": 0, "comma": False, "suffix": ""}),
    ("Most", None),
])
def test_count_spec(value, spec):
    assert kinds.count_spec(value) == spec


def _said(text, step=400):
    return [WordSpan(text=w, start_ms=i * step, end_ms=i * step + 300) for i, w in enumerate(text.split())]


def _rank(atlas, *, rank=1, stat=None, narration="Italy is number one.", fly_ms=900, last_ms=4700, items=(),
          speech_start=1000):
    spec = MapReelInputs.model_validate({"segments": [{
        "kind": "rank", "rank": rank, "country": "ITA", "narration": narration, "stat": stat,
        "items": [{"name": n} for n in items]}]}).segments[0]
    seg = ResolvedSegment(id="seg-1", index=0, kind="rank", codes=["ITA"], names={"ITA": "Italy"},
                          colours={"ITA": "#8a2be2"})
    landed = compose.segment_frame([shape_of(atlas.places["ITA"])], 1.0, safe=kinds.RANK_SAFE)
    ctx = kinds.KindContext(atlas=atlas, landed=landed, fly_ms=fly_ms, speech_start=speech_start, speech_end=3500,
                            last_ms=last_ms, spoken=_said(narration), images={})
    return kinds.build(seg, spec, ctx)


def test_the_rank_badge_follows_the_fill(atlas):
    for fly_ms, rank in ((900, 1), (0, 7)):
        built = _rank(atlas, rank=rank, fly_ms=fly_ms)
        fill = built.places[0]["fillMs"]
        assert built.extras["rank"] == {"n": rank, "atMs": fill + 150, "colour": "#8a2be2", "gold": rank == 1}
        assert built.resolved["rank"] == built.extras["rank"]
        assert "stat" not in built.extras


def test_a_stat_heard_in_the_narration_pops_at_its_word(atlas):
    built = _rank(atlas, stat={"value": "70%", "label": "of the olive oil"}, narration="Italy makes 70% of it.")
    stat = built.extras["stat"]
    assert stat["atMs"] == 1000 + 2 * 400  # speech start + the third word
    assert stat["value"] == "70%" and stat["label"] == "of the olive oil"
    assert stat["count"] == kinds.count_spec("70%")
    assert built.resolved["stat"] == stat


def test_a_stat_not_heard_pops_after_the_fill(atlas):
    built = _rank(atlas, stat={"value": "70%"}, narration="Italy makes most of it.")
    assert built.extras["stat"]["atMs"] == built.places[0]["fillMs"] + 600
    assert built.extras["stat"]["label"] is None
    late = _rank(atlas, stat={"value": "70%"}, narration="Italy makes most of it.", last_ms=1000)
    assert late.extras["stat"]["atMs"] == 1000


def test_rank_cues_ding_and_thud_and_items_pop(atlas):
    built = _rank(atlas, stat={"value": "70%"}, narration="Italy makes 70% with Gucci and Prada.",
                  items=["Gucci", "Prada"])
    assert (built.extras["rank"]["atMs"], "ding") in built.cues
    assert (built.extras["stat"]["atMs"], "thud") in built.cues
    assert [c for c in built.cues if c[1] == "pop"] == [(it["atMs"], "pop") for it in built.items]
    assert len(built.items) == 2
    # the items stay below the badge and the card: inside the rank safe area
    assert all(it["y"] >= kinds.RANK_SAFE[1] * 1920 for it in built.items)


def test_a_stat_heard_before_the_badge_waits_for_it(atlas):
    built = _rank(atlas, stat={"value": "70%"}, narration="70% is Italy's.", speech_start=0)
    assert built.extras["rank"]["atMs"] == 850  # fill 700 + 150; the value is said at 0
    assert built.extras["stat"]["atMs"] == 850
    late = _rank(atlas, stat={"value": "70%"}, narration="70% is Italy's.", speech_start=0, last_ms=500)
    assert late.extras["stat"]["atMs"] == 500  # still clamped to the segment's last pop
