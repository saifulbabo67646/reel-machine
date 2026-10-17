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


def test_panel_rects_stack_full_width_rows():
    assert kinds.panel_rects(2) == [(0, 0, 1080, 960), (0, 960, 1080, 960)]
    assert kinds.panel_rects(3) == [(0, 0, 1080, 640), (0, 640, 1080, 640), (0, 1280, 1080, 640)]


def _versus(atlas, narration, stats=("125M", "335M"), countries=("JPN", "USA"), speech_start=1000, last_ms=7700):
    from reelmachine.recipes.mapreel.geometry import frame_for

    spec = MapReelInputs.model_validate({"segments": [{
        "kind": "versus", "countries": list(countries), "narration": narration,
        "stats": [{"value": v, "label": "people"} for v in stats]}]}).segments[0]
    codes = [atlas.resolve(c).code for c in countries]
    seg = ResolvedSegment(id="seg-1", index=0, kind="versus", codes=codes,
                          names={c: atlas.places[c].name for c in codes},
                          colours=dict(zip(codes, ("#8a2be2", "#e0201b", "#1e90ff"))))
    rects = kinds.panel_rects(len(codes))
    panels = []
    for k, (code, rect) in enumerate(zip(codes, rects)):
        frame = frame_for(shape_of(atlas.places[code]).main, view=(rect[2], rect[3]), safe=kinds.PANEL_SAFE)
        panels.append({"frame": frame.to_dict(), "layer": f"/tmp/panel-{k}.jpg", "bbox": [0, 0, 1, 1],
                       "rect": list(rect)})
    union = compose.segment_frame([shape_of(atlas.places[c]) for c in codes], 1.0)
    ctx = kinds.KindContext(atlas=atlas, landed=union, fly_ms=0, speech_start=speech_start, speech_end=5000,
                            last_ms=last_ms, spoken=_said(narration), images={}, panels=panels)
    return kinds.build(seg, spec, ctx)


def test_a_versus_stat_pops_when_its_value_is_heard(atlas):
    built = _versus(atlas, "Japan has 125M people and America has 335M people")
    panels = built.extras["panels"]
    assert [p["stat"]["atMs"] for p in panels] == [1000 + 2 * 400, 1000 + 7 * 400]
    assert [p["stat"]["value"] for p in panels] == ["125M", "335M"]
    assert panels[0]["stat"]["count"] == kinds.count_spec("125M")
    assert [p["enterMs"] for p in panels] == [0, 120]
    assert [p["rect"] for p in panels] == [[0, 0, 1080, 960], [0, 960, 1080, 960]]
    assert panels[1]["layers"] == [{"url": "layers/panel-1.jpg", "bbox": [0, 0, 1, 1]}]
    assert built.links == {"layers/panel-0.jpg": Path("/tmp/panel-0.jpg"), "layers/panel-1.jpg": Path("/tmp/panel-1.jpg")}
    assert panels[0]["name"] == "Japan" and panels[0]["place"]["code"] == "JPN" and panels[0]["place"]["rings"]
    assert built.places == [] and built.items == []
    assert built.extras["vs"] == {"atMs": 250}
    assert sorted(built.cues) == sorted([(0, "whoosh"), (250, "boom"), (1800, "thud"), (3800, "thud")])
    assert [p["stat"]["atMs"] for p in built.resolved["panels"]] == [1800, 3800]


def test_a_versus_stat_pops_on_its_country_name_when_its_value_is_not_said(atlas):
    built = _versus(atlas, "Japan is small but the United States of America is huge")
    assert [p["stat"]["atMs"] for p in built.extras["panels"]] == [1000, 1000 + 5 * 400]


def test_an_unheard_versus_stat_is_spread_and_clamped(atlas):
    built = _versus(atlas, "One of these is bigger than the other one")
    times = [p["stat"]["atMs"] for p in built.extras["panels"]]
    assert times[0] < times[1] <= 7700
    late = _versus(atlas, "One of these is bigger than the other one", last_ms=900)
    assert all(p["stat"]["atMs"] <= 900 for p in late.extras["panels"])
