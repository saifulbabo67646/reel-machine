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


REGIONS = Path(__file__).parent / "fixtures" / "mapreel" / "regions.geojson"


@pytest.fixture(scope="module")
def region_atlas():
    from reelmachine.recipes.mapreel.regions import RegionAtlas

    return RegionAtlas.load(REGIONS)


def _regions(atlas, region_atlas, narration, regions=None, speech_start=1000, last_ms=7700):
    regions = regions or [{"region": "Texas", "items": [{"name": "BBQ"}]},
                          {"region": "US-CA", "items": [{"name": "Avocado"}]}]
    spec = MapReelInputs.model_validate({"segments": [{
        "kind": "regions", "country": "USA", "narration": narration, "regions": regions}]}).segments[0]
    found = [region_atlas.resolve(r["region"], "USA") for r in regions]
    seg = ResolvedSegment(id="seg-1", index=0, kind="regions", codes=["USA"],
                          names={"USA": "United States of America"}, colours={"USA": "#8a2be2"},
                          regions=[{"code": p.code, "name": p.name, "colour": c}
                                   for p, c in zip(found, ("#e0201b", "#3333ff", "#f28c0f"))])
    landed = compose.segment_frame([shape_of(atlas.places["USA"])], 1.0)
    ctx = kinds.KindContext(atlas=atlas, landed=landed, fly_ms=900, speech_start=speech_start, speech_end=5000,
                            last_ms=last_ms, spoken=_said(narration), images={}, regions=region_atlas)
    return kinds.build(seg, spec, ctx), landed


def test_a_region_fills_when_its_name_is_heard(atlas, region_atlas):
    built, _ = _regions(atlas, region_atlas, "Texas has BBQ and California has avocados.")
    assert [(r["code"], r["fillMs"]) for r in built.extras["regions"]] == [("US-TX", 1000), ("US-CA", 2600)]
    assert built.resolved["regions"] == [{"code": "US-TX", "name": "Texas", "colour": "#e0201b", "fillMs": 1000},
                                         {"code": "US-CA", "name": "California", "colour": "#3333ff", "fillMs": 2600}]
    # the country is the faint backdrop with its ghost name; every state's border is drawn over it
    assert [p["code"] for p in built.places] == ["USA"] and built.extras["countryFaint"] is True
    assert len(built.extras["borders"]) >= len(region_atlas.in_country("USA"))
    assert all(len(ring) >= 3 for ring in built.extras["borders"])
    assert (1000, "pop") in built.cues and (2600, "pop") in built.cues
    assert [c for c in built.cues if c[0] == built.items[0]["atMs"]]


def test_a_region_with_say_fills_at_the_say_words(atlas, region_atlas):
    built, _ = _regions(atlas, region_atlas, "Texas has BBQ and the Golden State has avocados.",
                        regions=[{"region": "Texas"}, {"region": "US-CA", "say": "the Golden State"}])
    assert [r["fillMs"] for r in built.extras["regions"]] == [1000, 2600]


def test_an_unheard_region_fills_on_its_first_item_or_is_spread(atlas, region_atlas):
    built, _ = _regions(atlas, region_atlas, "Brisket is everywhere, but so is the avocado.",
                        regions=[{"region": "Texas", "items": [{"name": "Brisket"}]}, {"region": "US-CA"}],
                        last_ms=1500)
    texas, california = built.extras["regions"]
    assert texas["fillMs"] == 1000 and 1000 < california["fillMs"] <= 1500


def test_an_item_never_pops_before_its_region_fills(atlas, region_atlas):
    # BBQ is said first, but it is not Texas's first item, so Texas fills on its name
    built, _ = _regions(atlas, region_atlas, "BBQ is huge, and so is Texas.",
                        regions=[{"region": "Texas", "items": [{"name": "Oil"}, {"name": "BBQ"}]}])
    (texas,) = built.extras["regions"]
    assert texas["fillMs"] == 1000 + 6 * 400
    assert all(it["atMs"] >= texas["fillMs"] for it in built.items)
    assert all((it["atMs"], "pop") in built.cues for it in built.items)


def test_items_lie_inside_their_region(atlas, region_atlas):
    import numpy as np

    from reelmachine.recipes.mapreel.geometry import inside, to_screen

    regions = [{"region": "Texas", "items": [{"name": n} for n in ("BBQ", "Oil", "Rodeo")]},
               {"region": "US-CA", "items": [{"name": n} for n in ("Avocado", "Tech")]}]
    built, landed = _regions(atlas, region_atlas, "Texas has BBQ, oil and rodeo; California avocado and tech.",
                             regions=regions)
    assert [it["name"] for it in built.items] == ["BBQ", "Oil", "Rodeo", "Avocado", "Tech"]
    owners = [0, 0, 0, 1, 1]
    for item, k in zip(built.items, owners):
        rings_px = [to_screen(landed, np.asarray(r)) for r in built.extras["regions"][k]["rings"]]
        assert inside(rings_px, item["x"], item["y"]), item
        assert 120 <= item["size"] <= 220


def test_a_region_name_tag_never_covers_an_item(atlas, region_atlas):
    from reelmachine.recipes.mapreel.geometry import BADGE_HALF_H

    from reelmachine.recipes.mapreel.geometry import BADGE_HALF_W

    built, landed = _regions(atlas, region_atlas, "Texas has BBQ and California has avocados.")
    for region in built.extras["regions"]:
        tag_x = (region["label"]["x"] - landed.x) * landed.s + 540
        tag_y = (region["label"]["y"] - landed.y) * landed.s + 960
        half_w = 0.4 * kinds.TAG_PX * len(region["tag"])
        for item in built.items:
            apart_x = abs(tag_x - item["x"]) >= half_w + BADGE_HALF_W * item["size"]
            apart_y = abs(tag_y - item["y"]) >= kinds.TAG_PX / 2 + BADGE_HALF_H * item["size"]
            assert apart_x or apart_y, (region["tag"], tag_x, tag_y, item)


def test_region_name_tags_never_cover_each_other():
    from reelmachine.recipes.mapreel.geometry import Frame

    frame = Frame(0.5, 0.5, 2000.0)
    same = {"x": 0.5, "y": 0.5}
    boxes = kinds.tag_boxes([("Osaka Prefecture", same), ("Tokyo", same), ("Kyoto", same)], [], frame)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert abs(a[0] - b[0]) >= a[2] + b[2] or abs(a[1] - b[1]) >= a[3] + b[3], (a, b)


def test_a_small_region_keeps_its_items_on_its_main_body(atlas, region_atlas):
    import numpy as np

    from reelmachine.recipes.mapreel.geometry import inside, merc, to_screen

    narration = "Tokyo has sushi."
    spec = MapReelInputs.model_validate({"segments": [{
        "kind": "regions", "country": "JPN", "narration": narration,
        "regions": [{"region": "Tokyo", "items": [{"name": "Sushi"}]}]}]}).segments[0]
    seg = ResolvedSegment(id="seg-1", index=0, kind="regions", codes=["JPN"], names={"JPN": "Japan"},
                          colours={"JPN": "#8a2be2"}, regions=[{"code": "JP-13", "name": "Tokyo", "colour": "#e0201b"}])
    landed = compose.segment_frame([shape_of(atlas.places["JPN"])], 1.0)
    ctx = kinds.KindContext(atlas=atlas, landed=landed, fly_ms=900, speech_start=1000, speech_end=3000,
                            last_ms=4000, spoken=_said(narration), images={}, regions=region_atlas)
    (sushi,) = kinds.build(seg, spec, ctx).items
    tokyo = shape_of(region_atlas.places["JP-13"])
    main = [r for r in tokyo.rings if r[:, 0].min() >= tokyo.main[0] - 1e-9 and r[:, 0].max() <= tokyo.main[2] + 1e-9
            and r[:, 1].min() >= tokyo.main[1] - 1e-9 and r[:, 1].max() <= tokyo.main[3] + 1e-9]
    x0, y0, x1, y1 = to_screen(landed, np.array([[tokyo.main[0], tokyo.main[1]], [tokyo.main[2], tokyo.main[3]]])).ravel()
    # the badge's anchor is over Tokyo itself, not the islands a thousand km south
    assert x0 - 60 <= sushi["x"] <= x1 + 60 and y0 - 60 <= sushi["y"] <= y1 + 60, (sushi, (x0, y0, x1, y1))


def test_a_blocked_tag_moves_sideways_before_it_stacks_up():
    from reelmachine.recipes.mapreel.geometry import Frame

    frame = Frame(0.5, 0.5, 2000.0)
    item = {"x": 540.0, "y": 960.0, "size": 120}
    (box,) = kinds.tag_boxes([("Tokyo", {"x": 0.5, "y": 0.5})], [item], frame)
    assert box[1] == 960.0 and abs(box[0] - 540) >= box[2] + 0.55 * 120  # same height, beside the badge
    # no room either side: up it goes
    wide = {"x": 540.0, "y": 960.0, "size": 900}
    (box,) = kinds.tag_boxes([("Tokyo", {"x": 0.5, "y": 0.5})], [wide], frame)
    assert box[1] < 960 - 0.3 * 900


def test_tags_are_short_names_and_items_sit_on_plates(atlas, region_atlas):
    built, _ = _regions(atlas, region_atlas, "Texas has BBQ and the Golden State has avocados.",
                        regions=[{"region": "Texas", "items": [{"name": "BBQ"}]},
                                 {"region": "US-CA", "say": "the Golden State", "items": [{"name": "Avocado"}]}])
    assert [r["tag"] for r in built.extras["regions"]] == ["Texas", "the Golden State"]
    # every region item has a plate in its region's colour, so it reads over sea or dark land
    assert [it["plate"] for it in built.items] == ["#e0201b", "#3333ff"]
    # the tag's anchor stays on the region's label point, wherever the tag itself moved
    assert all({"x", "y"} == set(r["anchor"]) for r in built.extras["regions"])


@pytest.mark.parametrize("name, tag", [("Ōsaka Prefecture", "Ōsaka"), ("Punjab Province", "Punjab"),
                                       ("Moscow Oblast", "Moscow"), ("Texas", "Texas"), ("Prefecture", "Prefecture")])
def test_a_generic_suffix_is_dropped_from_a_tag(name, tag):
    assert kinds.tag_text(name) == tag


def test_an_unheard_region_is_warned_and_no_state_fills_before_the_country(atlas, region_atlas):
    built, _ = _regions(atlas, region_atlas, "Texas first, then the west.", speech_start=0,
                        regions=[{"region": "Texas"}, {"region": "US-CA"}])
    country = built.places[0]["fillMs"]
    assert all(r["fillMs"] >= country for r in built.extras["regions"])
    assert any("California" in w and "not heard" in w and "say" in w for w in built.warnings)
    assert not any("Texas" in w for w in built.warnings)


def test_a_regions_segment_hands_its_fills_to_the_next_segments_fade(atlas, region_atlas):
    built, _ = _regions(atlas, region_atlas, "Texas has BBQ and California has avocados.")
    shown = built.shown
    assert shown[0]["code"] == "USA" and shown[0]["alpha"] == 0.25
    assert [p["code"] for p in shown[1:]] == ["US-TX", "US-CA"] and all("alpha" not in p for p in shown[1:])
